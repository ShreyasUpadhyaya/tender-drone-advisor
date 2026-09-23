# Codex implementation playbook

## Package boundaries

Separate web presentation, API transport, workflow execution, catalog/domain rules, solver math, and evaluation fixtures. API/worker can share domain packages, but solver must not depend on FastAPI, database sessions, LangChain/LangGraph, network I/O, or clock state. Version IDs and typed snapshots are explicit solver inputs.

## LangChain and LangGraph responsibilities

Use LangChain only inside nodes for loaders, chunking, prompt templates, provider-neutral structured output, embeddings/retrievers, and narrow tool wrappers. Structured outputs use strict Pydantic/JSON schema. Store prompt name/version and model ID, never prompt bodies in user traces.

Use LangGraph as the workflow shell. Graph state includes trace/document/version IDs, attempts, safe node summaries, anchors, requirement-set ID, validation findings, catalog-version ID, retrieval IDs, solver-run ID, recommendation ID, review reason, error class. Protected storage holds raw text; UI state contains IDs, hashes, bounded redacted excerpts, and summaries.

Required routing:

```text
parse_document -> extract_requirements -> validate_requirements
validate_requirements -> retrieve_catalog -> solve_configuration -> generate_grounded_report
validate_requirements -> human_review
```

Transient parse/provider errors get classified bounded retries. Invalid model JSON gets one repair only. A second invalid response, low confidence, missing citation, unsupported claim, invalid value, or missing mandatory requirement enters `human_review` and results in `needs_review` pending human action. Never retry auth, policy, or validation failures blindly.

## Provider rules

- Adapter exposes typed extract/embed/grounded-report functions; graph does not import vendor SDK.
- CI uses deterministic fakes. Live provider smoke tests are manual, redacted, and non-blocking.
- Do not log prompts, raw documents, keys, headers, or full completions. Record model ID, prompt version, allowed latency/token/cost telemetry, and validation outcome.
- Model output cannot create catalog records, alter prices, select a non-solver configuration, waive constraints, or approve a result.

## Solver rules

Normalize inputs before solve: kg, m, m/s, W, Wh/J, documented temperature conversion, days, and paise. Reject unknown/ambiguous units. Evaluate lifecycle/availability, certification applicability, compatibility, structural mass/payload margin, power/battery margin, endurance/range margin, environment, integration, quantity/delivery, lead time, and commercial ceiling. Stable documented tie-breakers only.

`feasible` is existing configuration passes. `feasible_with_changes` is explicit compatible modified BOM passes. `not_feasible` is sufficiently specified mandatory requirements cannot be met. `needs_review` is insufficient evidence or invalid input. Every candidate lists constraints passed/failed. Cost is catalog BOM + explicit engineering/integration + explicit contingency, all paise; unknown values become risks/questions.

## Testing commands to establish in C01

```text
docker compose up --build
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web test
uv run pytest
uv run ruff check .
uv run mypy apps packages
docker compose run --rm api alembic upgrade head
pnpm --filter web test:e2e
uv run python -m packages.evals.run --provider=fake
```

Run focused checks per commit and full unit/integration/E2E/evaluation checks in C08; report exact executed commands/output.
