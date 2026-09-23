# Delivery plan — Thursday 24 September to Sunday 27 September 2026

Dates allocate work but never bypass approval. Every item is one small, reviewable commit. Following its stop report, no later commit may start until the owner sends the matching approval command.

## Commit and push protocol

After each approved implementation commit, stop and report its commit hash, changed files, test evidence, local/preview URLs, and known gaps. Ask exactly: `Ready to push Cxx?` Do not push until the owner explicitly replies `push Cxx`.

On `push Cxx`, create or use a GitHub repository named `tender-drone-advisor`, set it as `origin`, and push that exact approved commit. The repository is private by default unless the owner explicitly requests public visibility. If GitHub authentication or repository creation is blocked, stop and give the exact required user action without exposing secrets.

After a successful push, stop again. The next implementation commit starts only after `approve C(next)`; every later commit must be pushed before its successor begins.

## Thursday 24 September

### C01 — Terra

**Exact commit:** `chore: bootstrap tender advisor platform`

Scope: monorepo skeleton; Next.js shell; FastAPI health endpoint; Docker Compose services for PostgreSQL 16/pgvector, Redis, MinIO, API, worker, web; safe configuration template; baseline lint/type/test commands and CI scaffold. No tender retrieval, model call, or business logic.

Acceptance: `docker compose up --build` starts declared services; typed `GET /health` response; web shell loads; backend unit test and frontend lint/type checks pass.

Stop report: commands/service URLs, health result, changed files, test evidence, and environment blockers. Then stop.

**Approval command:** `approve C01`

### C02 — Terra

**Exact commit:** `feat: ingest tenders with traceable document text`

Scope: demo-scoped upload, object-store abstraction, document/version/job records, PDF/DOCX parse, OCR fallback interface for scans, page/span anchors, worker status, and source-excerpt endpoint. No external tender download.

Acceptance: upload a fixture digital PDF and scanned fixture; auditable status transitions; citation returns right page/excerpt; unsupported formats fail safely.

Stop report: upload result, page count, citation lookup, worker output, fixture provenance, and OCR limitation. Then stop.

**Approval command:** `approve C02`

## Friday 25 September

### C03 — Astra

**Exact commit:** `feat: extract validated tender requirements with evidence`

Scope: strict versioned schema for procurement, platform, performance, payload, integration, compliance, environment, quantity, delivery, warranty, commercial terms; LangChain loader/prompt/structured-output primitives; LangGraph parse/extract/validate/review state; citations/confidence; one JSON repair; review routing for ambiguous, unsupported, invalid, or missing mandatory fields.

Acceptance: fixture produces valid JSON; all populated critical fields are cited; malformed output repairs once then yields `needs_review`; trace captures safe node status/branch.

Stop report: requirement excerpt, citation coverage, confidence/review flags, trace, fake-provider result, review triggers. Then stop.

**Approval command:** `approve C03`

### C04 — Luna

**Exact commit:** `feat: add versioned drone capability catalog`

Scope: Pydantic/SQLAlchemy/Alembic CRUD and validated import for airframes, batteries, propulsion, payloads, cameras/sensors, communications, GCS/software, certifications, costs, lead times, inventory, availability, compatibility, configurations. Seed only authorized demonstration data with provenance.

Acceptance: valid create/update/import; reject invalid units, negative paise, incompatible attributes, invalid versions; compatible-candidate query works; response includes catalog version.

Stop report: migration/import-validation output, sample versioned record, compatibility query, test evidence, provenance, data assumptions. Then stop.

**Approval command:** `approve C04`

## Saturday 26 September

### C05 — Astra

**Exact commit:** `feat: solve feasible drone configurations and cost`

Scope: pure deterministic solver/ranker for mandatory constraints, compatibility, mass/power, range/endurance margins, environment, availability, lead time, BOM, engineering/integration cost, contingency, alternatives, rejected near-misses, risks, assumptions, clarification questions. Solver alone assigns outcome.

Acceptance: tests for existing, modified, impossible, incomplete cases; integer paise; explicit SI; repeated inputs give identical order/totals; every rejection names failed constraints.

Stop report: test matrix, top BOM/cost/margins, alternates/rejections, deterministic comparison, assumptions, review routes. Then stop.

**Approval command:** `approve C05`

### C06 — Astra

**Exact commit:** `feat: orchestrate cited recommendations and retrieval`

Scope: complete LangGraph, approved pgvector retrieval, grounded report layer, safe trace, evaluator, retry/error policy, human interruption. Graph is exactly `parse_document -> extract_requirements -> validate_requirements -> retrieve_catalog -> solve_configuration -> generate_grounded_report`; validation conditionally routes to `human_review`.

Acceptance: retrieval citations resolve; report cannot name non-solver component; graph shows status/timing/branch; retry is bounded; six-fixture evaluation runs with fakes in CI.

Stop report: rendered graph, safe trace, citation check, evaluation, fake CI, retry outcome, model/prompt IDs, failures. Then stop.

**Approval command:** `approve C06`

## Sunday 27 September

### C07 — Terra

**Exact commit:** `feat: deliver tender recommendation workspace`

Scope: operations UI for upload/status, clause review, configuration comparison, BOM/cost/risk, human approval, PDF/CSV export, and Workflow Inspector. Inspector renders LangGraph and per-run status, elapsed time, safe input/output summary, citations, branch; it never shows prompts, API keys, or sensitive full tender text.

Acceptance: browser happy path; loading/error/empty states; export reconciles to solver totals/citations; review branch visible; sensitive values absent.

Stop report: screenshots/recording, E2E result, export reconciliation, inspector evidence, redaction and UI gaps. Then stop.

**Approval command:** `approve C07`

### C08 — Terra

**Exact commit:** `chore: harden demo deployment and evaluation`

Scope: file/rate limits, catalog-edit role guard, audit events, retry/DLQ, OpenTelemetry, deployment manifests/runbook, clean demo seed, held-out rehearsal. Do not tune prompt/rules/catalog/tests from held-out fixtures before rehearsal.

Acceptance: clean deployment; integration/E2E pass; simulated provider failure reaches retry/review safely; held-out tender produces cited result or honest review/not-feasible; runbook supports demo.

Stop report: deploy command/result, test/eval summary, holdout record, failure simulation, demo checklist, residual risks/contingency. Then stop.

**Approval command:** `approve C08`

## Demo walkthrough

1. State scope and that the tender is held out.
2. Upload it; show document version, parse status, page/span evidence.
3. Review mandatory clauses, confidence, citations, and questions.
4. Show solver-backed comparison or explicit review/not-feasible branch.
5. Inspect BOM, paise cost breakdown, lead time, margins, alternatives, rejections, risks, assumptions.
6. Open Workflow Inspector for graph branch, timing, safe summaries, citations.
7. Set human approval and export cited PDF/CSV.

## Completion criteria

Eight independent approved commits exist; held-out tender result is reproducible by versions/trace ID; critical-field citation coverage is 100%; unsupported recommendation claims are zero; CI provider tests use fakes; and insufficient evidence honestly routes to review.
