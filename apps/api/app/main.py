from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.analysis.api import router as analysis_router
from app.catalog.api import router as catalog_router
from app.extraction.api import router as extraction_router
from app.rag.api import router as rag_router
from app.routes.documents import router as documents_router
from app.settings import get_settings


class HealthResponse(BaseModel):
    status: str
    service: str


app = FastAPI(title="Tender Drone Advisor API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
)
app.include_router(documents_router)
app.include_router(extraction_router)
app.include_router(catalog_router)
app.include_router(analysis_router)
app.include_router(rag_router)


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
