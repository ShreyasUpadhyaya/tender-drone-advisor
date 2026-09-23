# Exact start prompt for C01

```text
You are Terra, implementing Tender Drone Advisor commit C01 only.

Read README.md, AGENTS.md, docs/DELIVERY_PLAN.md, docs/ARCHITECTURE.md, and docs/CODEX_PLAYBOOK.md before editing. Today is Wednesday, 23 September 2026; C01 is scheduled for Thursday but may start only because the owner explicitly sent `approve C01`.

Implement exactly C01 with one conventional commit whose message is:
chore: bootstrap tender advisor platform

Scope is limited to a production-shaped monorepo skeleton: Next.js + TypeScript web shell; FastAPI + Pydantic + SQLAlchemy + Alembic API/domain skeleton; Docker Compose local services for PostgreSQL 16 with pgvector, Redis, MinIO, web, API, and worker; a typed GET /health endpoint; safe .env.example; baseline lint/type/test and CI configuration. Do not implement tender upload, parsing, OCR, catalog behavior, LangChain/LangGraph workflow, model calls, solver logic, retrieval, exports, or external integrations. Do not use API keys, fetch tenders, create credentials, or commit secrets.

Follow AGENTS.md strictly. Preserve user changes. Validate with C01 acceptance checks from DELIVERY_PLAN.md: Docker Compose build/start if environment permits, web shell load, GET /health, backend unit test, frontend lint/type checks. Do not claim an unrun check passed.

Create only the C01 commit. Then stop completely and return the required C01 stop report: commit SHA/message; changed files; exact commands/evidence; local service URLs; blockers; known limitations; and exactly this next gate: `approve C02`. Do not begin C02 under any circumstance, even if C01 finishes early.
```
