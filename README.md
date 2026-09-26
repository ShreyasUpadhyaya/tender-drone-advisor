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

## C03 requirement extraction

Start extraction with `POST /v1/document-versions/{version-id}/extractions` after ingestion completes.
The Redis worker runs a real LangGraph with LangChain model/prompt/parser adapters,
deterministic SI normalization, evidence checks, conflict detection, bounded repair
and persisted human-review items. Defaults use a credentials-free fake that honestly
routes missing requirements to review. No feasibility or cost decision is produced.
See [C03 extraction](docs/C03_EXTRACTION.md) for contracts, the graph, review APIs,
configuration, checks and exact opt-in live verification commands.

## C04 catalog and C05 deterministic analysis

See [catalog and retrieval](docs/C04_CATALOG_RETRIEVAL.md) for versioned synthetic
catalog imports, and [solver and costing](docs/C05_SOLVER_COSTING.md) for bounded
configuration generation, typed analysis APIs, immutable inputs, integer BOM
costing, review routing and reproducible Compose smoke checks.

C05 makes no LLM calls and does not imply engineering or flight approval.

## C06 retrieval and grounded orchestration

See [C06 RAG orchestration](docs/C06_RAG_ORCHESTRATION.md) for snapshot-isolated
pgvector search, typed grounded reports, safe node audits and durable human-review
interruption/resume. Local defaults use fake adapters; external processing requires
an explicit server setting and per-request authorization. C05 remains authoritative.

## C07 web workspace

Start the stack with `docker compose up --build -d`, then open
`http://localhost:3000`. For local frontend iteration use `cd apps/web`, `npm ci`,
and `npm run dev`. Upload a synthetic PDF, DOCX or TXT; wait for ingestion, run
extraction, inspect citations/issues, start analysis with a catalog UUID, inspect
BOM/cost, then generate the grounded report. Costs are backend paise rendered as
INR; the UI never calculates totals or grants approval.

See [C07 web application](docs/C07_WEB_APPLICATION.md) for the architecture,
polling/error model, privacy boundary and manual smoke procedure.

The visible **Local Demo Mode** banner is intentional: the local workspace does
not implement login or authorization. C08/production hardening must provide
authentication, RBAC, tenant isolation, signed uploads and per-user audit
attribution. Do not expose the local demo to untrusted networks.

## C08 quick start, demo and troubleshooting

The final local flow is credentials-free by default: fake extraction/RAG adapters
are used and `DEMO_MODE=true` is visible in the UI. Start with:

```powershell
$env:LLM_PROVIDER="fake"
$env:LLM_MODEL="fixture-v1"
$env:RAG_EMBEDDING_PROVIDER="fake"
$env:RAG_REPORT_PROVIDER="fake"
docker compose up --build -d
docker compose exec -T api alembic current
```

These explicit process overrides take precedence over any untracked `.env` used
for an earlier live-model gate. Verify the provider names before uploading test
data; never print secret values.

Then follow [the synthetic feasible and needs-review checklist](docs/DEMO_CHECKLIST.md).
For the full click-by-click upload, review-decision, manual-inventory, scenario,
BOM and export walkthrough, use [manual testing](docs/MANUAL_TESTING.md).
The feasible fixture is intentionally marked and is the only fake path that
returns deterministic cited requirements; normal fake inputs safely route to
review. For health, use `http://localhost:8000/health`; for dependency readiness
in demo mode, use `http://localhost:8000/v1/ops/readiness`.

If a service is unavailable, run `docker compose ps`, then
`docker compose logs --tail=100 api worker`. Do not paste `.env` values into logs
or issue reports. A `job_stale` response is recovered by repeating the original
idempotent action; it is not a reason to edit historical runs. See
[production readiness](docs/C08_PRODUCTION_READINESS.md) and the
[deployment runbook](docs/DEPLOYMENT_RUNBOOK.md) for implemented controls,
production integration boundaries and backup/rollback guidance.
