from fastapi.testclient import TestClient

from app.main import app


def test_local_web_origin_is_allowed_for_preflight() -> None:
    response = TestClient(app).options(
        "/v1/documents",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_unconfigured_origin_is_not_permitted() -> None:
    response = TestClient(app).get("/health", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in response.headers
