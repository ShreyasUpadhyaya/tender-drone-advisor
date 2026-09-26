"""Offline C08 end-to-end demo smoke.

Runs against the Compose API/worker with fake adapters only. It uploads the two
labelled, version-controlled fixtures, proves the feasible C05/RAG path and
proves that incomplete input remains review-required. It never reads test-data,
private tenders, .env values, or provider credentials.
"""

import argparse
import json
import mimetypes
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen


def request(
    base: str, path: str, data: object | None = None, content_type="application/json"
) -> dict:
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
    with urlopen(
        Request(base + path, data=data, headers={"Content-Type": content_type}), timeout=60
    ) as result:
        return json.load(result)


def multipart_upload(base: str, path: Path) -> dict:
    boundary = f"c08-demo-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "text/plain"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return request(base, "/v1/documents", body, f"multipart/form-data; boundary={boundary}")


def wait_for(base: str, path: str, states: set[str], label: str) -> dict:
    for _ in range(180):
        value = request(base, path)
        if value["state"] in states:
            return value
        time.sleep(0.25)
    raise AssertionError(f"{label} did not reach a terminal state")


def upload_and_extract(base: str, fixture: Path) -> tuple[dict, dict]:
    upload = multipart_upload(base, fixture)
    document = wait_for(
        base, f"/v1/documents/{upload['document_id']}", {"completed", "failed"}, "ingestion"
    )
    assert document["state"] == "completed", document
    # Send an empty JSON object so urllib uses POST for this body-less endpoint.
    run = request(base, f"/v1/document-versions/{upload['document_version_id']}/extractions", {})
    extraction = wait_for(
        base,
        f"/v1/extraction-runs/{run['trace_id']}",
        {"completed", "needs_review", "failed"},
        "extraction",
    )
    assert extraction["state"] != "failed", extraction
    return upload, extraction


def wait_analysis(base: str, analysis_id: str) -> dict:
    return wait_for(base, f"/v1/analyses/{analysis_id}", {"completed", "failed"}, "analysis")


def wait_rag(base: str, run_id: str) -> dict:
    return wait_for(
        base,
        f"/v1/rag/runs/{run_id}",
        {"completed", "failed", "awaiting_review"},
        "grounded report",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--feasible", required=True, type=Path)
    parser.add_argument("--needs-review", required=True, type=Path)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument(
        "--analysis-date",
        default=datetime.now(UTC).date().isoformat(),
        help="ISO date included in analysis identity; override to replay a specific run.",
    )
    args = parser.parse_args()
    base = args.base.rstrip("/")

    health = request(base, "/health")
    assert health["status"] == "ok"
    catalog = request(base, "/v1/catalog/import", json.loads(args.seed.read_text(encoding="utf-8")))

    _, feasible = upload_and_extract(base, args.feasible)
    requirements = request(base, f"/v1/extraction-runs/{feasible['trace_id']}/requirements")
    assert feasible["state"] == "completed" and len(requirements) == 4, feasible
    assert all(item["requirement"]["validated_evidence"] for item in requirements)
    analysis = request(
        base,
        "/v1/analyses",
        {
            "extraction_run_id": feasible["trace_id"],
            "catalog_version_id": catalog["catalog_version_id"],
            "analysis_date": args.analysis_date,
        },
    )
    solved = wait_analysis(base, analysis["id"])
    assert solved["state"] == "completed" and solved["status"] == "feasible", solved
    configurations = request(base, f"/v1/analyses/{analysis['id']}/configurations")
    assert configurations["configurations"], configurations
    first = configurations["configurations"][0]
    bom = request(base, f"/v1/analyses/{analysis['id']}/configurations/{first['id']}/bom")
    assert bom["lines"] and bom["cost"]["total_paise"] >= bom["cost"]["material_paise"]

    index = request(base, "/v1/rag/indexes", {"analysis_id": analysis["id"]})
    index_ready = wait_for(base, f"/v1/rag/indexes/{index['id']}", {"completed", "failed"}, "index")
    assert index_ready["state"] == "completed", index_ready
    graph = request(base, "/v1/rag/runs", {"analysis_id": analysis["id"]})
    report_run = wait_rag(base, graph["id"])
    assert report_run["state"] == "completed", report_run
    report = request(base, f"/v1/rag/runs/{graph['id']}/report")
    citations = request(base, f"/v1/rag/runs/{graph['id']}/citations")["citations"]
    nodes = request(base, f"/v1/rag/runs/{graph['id']}/nodes")["nodes"]
    assert report["status"] == "feasible" and citations and nodes

    _, review = upload_and_extract(base, args.needs_review)
    issues = request(base, f"/v1/extraction-runs/{review['trace_id']}/issues")
    assert review["state"] == "needs_review" and issues, review
    provisional = request(
        base,
        "/v1/analyses",
        {
            "extraction_run_id": review["trace_id"],
            "catalog_version_id": catalog["catalog_version_id"],
            "analysis_date": args.analysis_date,
        },
    )
    review_analysis = wait_analysis(base, provisional["id"])
    assert review_analysis["status"] == "needs_review", review_analysis

    print(
        json.dumps(
            {
                "provider": "fake",
                "feasible": {
                    "requirements": len(requirements),
                    "analysis_status": solved["status"],
                    "bom_lines": len(bom["lines"]),
                    "report_status": report["status"],
                    "citations": len(citations),
                    "audited_nodes": len(nodes),
                },
                "needs_review": {
                    "issue_count": len(issues),
                    "analysis_status": review_analysis["status"],
                },
                "private_data_used": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
