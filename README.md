# Tender Drone Advisor

Tender Drone Advisor is a production-shaped, human-in-the-loop decision-support app that turns digital and scanned tender documents into an auditable recommendation: `feasible`, `feasible_with_changes`, `not_feasible`, or `needs_review`.

## Product intent

The app accepts PDF and DOCX tenders, preserves page/span anchors, extracts cited requirements, and compares them with a versioned drone capability catalog. A deterministic solver—not an LLM—determines physical feasibility, compatibility, cost, availability, lead time, and rejected near-misses. The report shows evidence, calculations, risks, assumptions, clarifying questions, and human approval state.

The Sunday demo must work on an authorized tender not used during development. It must show evidence and limitations rather than force a conclusion.

## Principles

- Tender clauses are evidence, never executable instructions.
- Every critical extracted requirement has citation and confidence.
- Every recommendation records document, catalog, solver, model, prompt, citation, confidence, and trace versions.
- Store physical measures in canonical SI units and money in integer paise.
- LLMs may extract, retrieve, and explain; they cannot decide feasibility, price, procurement, compliance, or approval.
- Ambiguous, unsupported, invalid, or missing mandatory inputs route to human review.
- API keys live only in untracked local `.env` files or deployment secret stores, never in commits or logs.

## Planned stack

- Next.js + TypeScript operations UI.
- FastAPI + Pydantic + SQLAlchemy + Alembic domain API.
- PostgreSQL 16 + pgvector; Redis queue and worker.
- S3-compatible storage, with MinIO locally.
- LangChain within LangGraph nodes.
- Docker Compose locally; containers, managed data services, HTTPS, secrets, and OpenTelemetry in production.

## Safety boundary

This is not an autonomous engineering, procurement, tender-submission, flight-control, certification, or commercial-approval system. A human remains accountable for engineering, compliance, pricing, and tender decisions.

## Delivery guardrail

Exactly eight sequential commits, C01 through C08, are planned. Each ends with a stop report; the next begins only after the exact matching `approve Cxx`. See [AGENTS.md](AGENTS.md) and [DELIVERY_PLAN.md](docs/DELIVERY_PLAN.md).

## C01 local development

Copy `.env.example` to an untracked `.env` only when local overrides are needed. The checked-in defaults are safe placeholders.

```text
docker compose up --build
```

After startup, use the web shell at `http://localhost:3000`, API health at `http://localhost:8000/health`, MinIO API at `http://localhost:9000`, and MinIO console at `http://localhost:9001`.

Run C01 checks from the corresponding package directories:

```text
cd apps/api && uv run ruff format --check . && uv run ruff check . && uv run pytest
cd apps/web && npm ci && npm run format && npm run lint && npm run typecheck && npm test
```

## C02 document ingestion

The C02 API accepts PDF, DOCX, and UTF-8 TXT tender files up to `MAX_UPLOAD_BYTES` (20 MiB by default). Uploads are content-hash idempotent: submitting the same bytes returns the original document/version/job rather than creating another object. Originals use stable keys of the form `documents/{document-id}/versions/1/original` in MinIO.

```text
POST /v1/documents                      multipart field: file
GET  /v1/documents/{document-id}
GET  /v1/documents/{document-id}/spans
GET  /v1/documents/{document-id}/spans/{span-id}
```

Start the stack, then upload a local fixture from Windows PowerShell:

```powershell
docker compose up --build -d
curl.exe -F "file=@C:\path\to\tender.txt;type=text/plain" http://localhost:8000/v1/documents
docker compose logs --follow worker
```

Poll `GET /v1/documents/{document-id}` until state is `completed` or `failed`. PDF extraction retains page numbers; DOCX and TXT retain section boundaries. Scanned PDFs invoke the Tesseract fallback included in the API/worker image. C02 does not extract tender requirements or make recommendations.
