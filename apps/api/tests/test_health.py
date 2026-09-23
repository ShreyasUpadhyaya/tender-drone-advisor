from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_typed_healthy_response() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "tender-drone-advisor-api"}
