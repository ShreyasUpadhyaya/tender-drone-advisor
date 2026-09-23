from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from reportlab.pdfgen.canvas import Canvas

from app.ingestion import validate_upload
from app.parsers import ParsedChunk, parse_document
from app.tasks import ingest_document_task


def _digital_pdf() -> bytes:
    output = BytesIO()
    canvas = Canvas(output)
    canvas.drawString(72, 720, "Tender reference: TDA-2026-01")
    canvas.showPage()
    canvas.drawString(72, 720, "Payload requirement: thermal camera")
    canvas.save()
    return output.getvalue()


def _blank_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


def test_upload_is_idempotent_and_preserves_source_spans(client, storage, monkeypatch) -> None:
    tender = (Path(__file__).parent / "fixtures" / "messy-tender.txt").read_bytes()
    upload = client.post(
        "/v1/documents",
        files={"file": ("messy-tender.txt", tender, "text/plain")},
    )
    assert upload.status_code == 201
    created = upload.json()
    assert created["state"] == "uploaded"
    assert created["idempotent"] is False

    repeated = client.post(
        "/v1/documents",
        files={"file": ("messy-tender.txt", tender, "text/plain")},
    )
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert repeated.json()["document_id"] == created["document_id"]

    monkeypatch.setattr("app.tasks.get_storage", lambda: storage)
    ingest_document_task(created["document_version_id"])

    status_response = client.get(f"/v1/documents/{created['document_id']}")
    assert status_response.status_code == 200
    assert status_response.json()["state"] == "completed"
    assert status_response.json()["source_span_count"] == 2

    spans = client.get(f"/v1/documents/{created['document_id']}/spans").json()["spans"]
    assert spans[0]["section_name"] == "section-1"
    assert spans[0]["start_offset"] == 0
    source = client.get(f"/v1/documents/{created['document_id']}/spans/{spans[1]['span_id']}")
    assert source.status_code == 200
    assert source.json()["text"].startswith("PAYLOAD : thermal camera")


def test_file_validation_returns_structured_errors(client) -> None:
    unsupported = client.post(
        "/v1/documents", files={"file": ("tender.exe", b"x", "application/octet-stream")}
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["error"] == "unsupported_file_type"

    mismatch = client.post("/v1/documents", files={"file": ("tender.pdf", b"x", "text/plain")})
    assert mismatch.status_code == 415
    assert mismatch.json()["error"] == "content_type_mismatch"
    assert validate_upload("small.txt", "text/plain", 1, 1) == "text/plain"


def test_pdf_pages_and_scanned_style_ocr_fallback_are_traceable() -> None:
    digital = parse_document(_digital_pdf(), "application/pdf")
    assert [chunk.page_number for chunk in digital] == [1, 2]
    assert "thermal camera" in digital[1].text

    class FakeOcr:
        def extract(self, _: bytes) -> list[ParsedChunk]:
            return [ParsedChunk("RANGE 25KM / unclear payload?", 1, None, "ocr")]

    scanned = parse_document(_blank_pdf(), "application/pdf", ocr=FakeOcr())
    assert scanned[0].extraction_method == "ocr"
    assert scanned[0].page_number == 1
    assert "unclear payload" in scanned[0].text
