from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.extraction.api import router as extraction_router
from app.routes.documents import router as documents_router


class HealthResponse(BaseModel):
    status: str
    service: str


app = FastAPI(title="Tender Drone Advisor API", version="0.1.0")
app.include_router(documents_router)
app.include_router(extraction_router)


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
