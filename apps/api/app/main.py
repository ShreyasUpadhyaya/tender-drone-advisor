from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


app = FastAPI(title="Tender Drone Advisor API", version="0.1.0")


@app.get("/health", response_model=HealthResponse, tags=["health"])
def get_health() -> HealthResponse:
    """Return process health without checking later-stage dependencies."""
    return HealthResponse(status="ok", service="tender-drone-advisor-api")
