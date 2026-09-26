import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.analysis.api import router as analysis_router
from app.catalog.api import router as catalog_router
from app.extraction.api import router as extraction_router
from app.inventory.api import router as inventory_router
from app.observability import LOGGER, metrics
from app.ops import router as ops_router
from app.rag.api import router as rag_router
from app.rate_limit import enforce_rate_limit
from app.routes.documents import router as documents_router
from app.scenarios.api import router as scenarios_router
from app.settings import get_settings


class HealthResponse(BaseModel):
    status: str
    service: str


logging.basicConfig(level=get_settings().log_level.upper())
app = FastAPI(title="Tender Drone Advisor API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "authorization", "x-workspace-id", "x-request-id"],
)
app.include_router(documents_router)
app.include_router(extraction_router)
app.include_router(catalog_router)
app.include_router(analysis_router)
app.include_router(rag_router)
app.include_router(scenarios_router)
app.include_router(inventory_router)
app.include_router(ops_router)


@app.middleware("http")
async def secure_observed_request(request: Request, call_next):
    """Correlation, safe metrics, rate limiting and browser security headers."""
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        enforce_rate_limit(request)
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse(
            status_code=exc.status_code, content=exc.detail, headers=exc.headers
        )
    except Exception:  # noqa: BLE001 -- outer boundary must redact unknown failures
        LOGGER.exception(json.dumps({"event": "request_failed", "request_id": request_id}))
        response = JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "The request failed safely."},
        )
    duration = time.perf_counter() - started
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    # Keep FastAPI's local Swagger dependencies usable; API responses do not
    # render untrusted tender HTML, while browser-facing production CSP belongs
    # at the HTTPS ingress and Next.js deployment layer.
    if request.url.path not in {"/docs", "/openapi.json", "/redoc"}:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
    metrics.increment(
        "tda_http_requests_total",
        method=request.method,
        path=request.url.path,
        status=str(response.status_code),
    )
    metrics.observe(
        "tda_http_request_duration_seconds", duration, method=request.method, path=request.url.path
    )
    LOGGER.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "outcome": "success" if response.status_code < 400 else "failure",
            }
        )
    )
    return response


@app.exception_handler(HTTPException)
async def structured_http_error(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and {"error", "detail"} <= exc.detail.keys():
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "request_failed", "detail": str(exc.detail)},
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
def get_health() -> HealthResponse:
    """Return process health without checking later-stage dependencies."""
    return HealthResponse(status="ok", service="tender-drone-advisor-api")
