from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.db import _engine_options
from app.extraction.provider import fake_fixture_response
from app.models import AuditEvent, Document, DocumentVersion, IngestionJob, ProcessingState
from app.rate_limit import SlidingWindowLimiter
from app.recovery import mark_stale_jobs
from app.security import get_actor
from app.settings import Settings


def request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": [], "path": "/v1/documents"})


def test_demo_identity_is_explicit_and_production_without_oidc_fails() -> None:
    actor = get_actor(request(), None, None, Settings())
    assert actor.demo and actor.workspace_id == "local-demo"
    with pytest.raises(HTTPException) as exc:
        get_actor(
            request(),
            None,
            None,
            Settings(app_env="production", demo_mode=False, auth_mode="demo"),
        )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "auth_not_configured"


def test_signature_filename_and_headers_are_safe(client) -> None:
    malformed = client.post(
        "/v1/documents", files={"file": ("../../tender.pdf", b"not-a-pdf", "application/pdf")}
    )
    assert malformed.status_code == 415
    assert malformed.json()["error"] == "file_signature_mismatch"
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


def test_upload_creates_redacted_audit_event(client) -> None:
    response = client.post(
        "/v1/documents", files={"file": ("report.txt", b"Synthetic tender", "text/plain")}
    )
    assert response.status_code == 201
    from app.db import SessionLocal

    with SessionLocal() as db:
        event = db.query(AuditEvent).one()
        document = db.get(Document, response.json()["document_id"])
        assert event.action == "document.upload"
        assert event.details == {"idempotent": False, "byte_size": len(b"Synthetic tender")}
        assert document.workspace_id == "local-demo"


def test_rate_window_and_safe_stale_recovery() -> None:
    limiter = SlidingWindowLimiter()
    assert limiter.check("upload:example", 1, now=1) is None
    assert limiter.check("upload:example", 1, now=2) is not None
    assert limiter.check("upload:example", 1, now=62) is None

    from app.db import SessionLocal

    with SessionLocal() as db:
        document = Document(content_sha256="a" * 64, workspace_id="local-demo")
        version = DocumentVersion(
            document=document,
            version_number=1,
            original_filename="demo.txt",
            content_type="text/plain",
            byte_size=1,
            storage_key="test/stale",
        )
        job = IngestionJob(document_version=version, state=ProcessingState.PROCESSING)
        db.add_all([document, version, job])
        db.commit()
        job.updated_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
        result = mark_stale_jobs(db, stale_seconds=60)
        assert result["ingestion"] == 1
        assert job.state == ProcessingState.FAILED
        assert job.error_code == "job_stale"


def test_explicit_c08_fixture_is_the_only_fake_feasible_path() -> None:
    fixture = fake_fixture_response(
        {"spans": '[{"span_id":"span-1","text":"TDA_DEMO_FEASIBLE synthetic"}]'}
    )
    ordinary = fake_fixture_response({"spans": '[{"span_id":"span-1","text":"ordinary"}]'})
    import json

    assert len(json.loads(fixture)["requirements"]) == 4
    assert json.loads(ordinary)["requirements"] == []


def test_postgres_engine_disables_server_prepared_statements() -> None:
    options = _engine_options("postgresql+psycopg://example")
    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {"prepare_threshold": None}
