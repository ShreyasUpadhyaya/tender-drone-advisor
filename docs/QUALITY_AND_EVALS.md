# Quality and evaluation plan

## Corpus governance

Use exactly six public or explicitly authorized tender fixtures. A manifest records source, license/authorization, document version/hash, format, scan quality, language, and purpose. Four are development fixtures; two are held out for unseen-tender rehearsal. Holdouts must not influence prompts, schemas, retrieval chunks, catalog data, solver rules, golden answers, or regression tests before C08. No tender is fetched during planning.

Each fixture has an answer key written or approved by a qualified human. It identifies source anchors, mandatory/optional clauses, normalized SI values/units, uncertainty labels, expected outcome or permitted set, clarification questions, expected compatible/rejected catalog facts, cost-input provenance, and limitations. It separates extraction facts from engineering judgment.

## Metrics and targets

- Critical-field citation coverage: 100%; denominator is every populated critical requirement.
- Unsupported recommendation claims: zero; components, numbers, costs, lead times, certifications, and feasibility claims trace to evidence/catalog/solver.
- Schema validity after repair, measured first-response and one-repair separately.
- Extraction field precision/recall/F1, normalization accuracy, anchor validity, confidence calibration, mandatory classification.
- Solver outcome correctness, rejection correctness, repeatability, paise reconciliation, SI conversions, boundary margins.
- Workflow branch correctness, retry classification, review routing, trace completeness, redaction checks.
- Product parse success, E2E completion, export reconciliation, accessibility smoke, node latency.

Set numeric thresholds only after baseline; never hide weak/unmeasurable metrics. A safe review route is a correct outcome when evidence is inadequate.

## Test layers

- Unit: normalization, schemas, citations, catalog validation, solver arithmetic/constraints, state routing, redaction.
- Contract: OpenAPI/Pydantic responses and migrations.
- Integration: Postgres/pgvector, Redis worker, MinIO, parsing/OCR, API/worker workflow.
- End-to-end: upload through review/report/export paths.
- Evaluation: all six fixtures, locked answer keys, fake providers.
- Resilience/security: invalid document, malformed model JSON, timeout, missing catalog facts, retry/DLQ, redaction, file/rate limits, authorization.

Provider-dependent CI tests use fakes/fixtures. Live keys/network are manual opt-in smoke tests with redacted output and never required for CI.

## Failure behavior

Invalid JSON gets exactly one constrained repair. If it remains invalid, citations are missing for a critical value, confidence is below policy, a unit/value is invalid, catalog data is absent, or a claim is ungrounded, return `needs_review` with reason/questions. Solver contradictions cannot be explained away by an LLM. Recoverable parser/model errors have bounded retry; nonrecoverable errors fail safely with operator-visible status and audit event.

## Evaluation evidence artifact

C06/C08 emits versioned JSON and readable summary with fixture ID (not sensitive text), document hash, catalog/solver/model/prompt versions, trace ID, per-field result, citation check, solver result, grounding audit, routing result, latency, and failure category. Holdout output is rehearsal evidence, not training data.
