# C08 production readiness

## Implemented controls

The local stack remains deliberately open only when `DEMO_MODE=true` outside a
production environment. It assigns the visible `local-demo-operator` identity
and `local-demo` workspace so the demo runs without a login screen. Production
cannot use demo mode: protected routes require `AUTH_MODE=oidc`; until a signed
OIDC/JWT verifier is wired, they fail closed with `auth_not_configured`.

Roles are `admin`, `reviewer`, and `viewer`. All C02–C06 routes require an actor;
catalog writes require admin, while upload, extraction, analysis and RAG starts
require admin or reviewer. Documents carry a workspace field and document reads
are workspace checked in non-demo mode. The existing synthetic catalog is global
in this case study; a production rollout must make catalog versions workspace
scoped before allowing tenant-managed catalog data.

Uploads sanitize display filenames, validate extension plus declared MIME type
and PDF/DOCX/TXT signatures, limit bytes and parsed page count, and use opaque
storage keys. Invalid inputs return stable safe codes. The in-process sliding
window limiter bounds upload and generation starts; production must replace it
with a Redis/shared limiter at ingress so it works across replicas.

The API adds correlation IDs, security headers, structured redacted request and
stage logs, append-only audit events, local metrics, `/health`, `/v1/ops/live`,
`/v1/ops/readiness`, `/v1/ops/status`, admin-only `/v1/ops/metrics`, and
admin-only stale-job recovery. The readiness probe tests PostgreSQL, Redis,
object storage and whether the configured model provider is usable without
printing credentials. Technical LangGraph audit records remain separate from
normal user pages.

Jobs are at-least-once, not exactly-once. Immutable input identities prevent
duplicate durable results and provider work where existing worker claims apply.
Stale queued/running work is marked failed with `job_stale`; an operator repeats
the original idempotent request rather than modifying audit history. Existing
provider retry budgets remain bounded and C03/C06 route exhausted work to safe
failure or review.

The PostgreSQL engine disables psycopg server-prepared statements by default
(`PSYCOPG_PREPARE_THRESHOLD` unset). This is a safe case-study setting for the
pooled worker pipeline and prevents prepared-statement name collisions after an
interrupted/replayed pipeline. Profile it before changing it in a high-throughput
deployment.

## Production integration boundary

Implement `JwtVerifier.verify` with a JWKS-caching OIDC library, enforcing issuer,
audience, expiry, signing algorithm and workspace claim. Populate `Actor` only
from validated claims; never accept a browser-supplied role or workspace. Run
behind HTTPS ingress, set production CORS to the approved web origin, use signed
short-lived uploads, and feed audit events into a protected retention system.

No key, prompt, source document body, authorization header, provider completion
or raw exception is written by C08 logging. Treat metadata such as filenames and
timing as sensitive operational data in production.

## Status and recovery

`GET /health` is process liveness. `GET /v1/ops/readiness` is dependency-aware
readiness. In production, an authenticated admin may call
`POST /v1/ops/recovery/stale`; it safely terminalizes stale jobs, preserving input,
audit and idempotency history. It does not retry provider calls automatically.

## Reviewer workspace and scenario estimates

An extraction that completes with zero citation-valid requirements is an explicit
review outcome, never a successful empty table. `GET /v1/extraction-runs/{run_id}/review-workspace`
returns accepted/rejected counts, safe failed-stage data, source-span availability,
linked review issues and the next safe action. A failed extraction retry creates a
new immutable run linked to the original failed attempt.

`POST /v1/extraction-runs/{run_id}/review-decisions` appends the reviewer,
timestamp, rationale, optional before/after values and source-span reference.
Evidence-backed acceptance or corrections require an in-version source span; the
original C03 requirement and audit history are never overwritten.

The reviewer identity is derived from the authenticated actor, never trusted from
the request body. A changed decision appends a new event linked through
`supersedes_event_id`; both the current and superseded decisions remain auditable.

Manual inventory uses workspace-scoped records plus immutable count/availability
versions. Creation requires `admin`; later versions require the same admin subject
that owns the record. Local Demo Mode visibly simulates this with
`local-demo-operator`. Catalog-linked versions can be snapshotted into a scenario;
unlisted stock remains pending catalog/engineering validation and cannot enter C05.

`POST /v1/scenarios` creates an immutable versioned internal-estimate snapshot.
It copies cited tender facts unchanged and persists internal assumptions separately,
with explicit units, deterministic normalization and rationale. C05 runs against
that snapshot; an optional SKU constraint filters only its catalog component
category. Each scenario keeps four separate labels: Tender requirements verified;
Engineering/catalog feasibility estimated; Assumptions outstanding; and
Bid/compliance review required.

An internal scenario estimate is not proof of tender compliance, engineering
approval, bid readiness, procurement approval or flight certification. Missing
prices and engineering-envelope data remain blockers rather than fabricated values.

## Known limits

This is a case-study control set, not a security certification. The local limiter
is process-local, the OIDC verifier is intentionally an integration interface,
catalog tenancy is a documented next migration, and external telemetry exporter
configuration is not enabled by default. C05 deterministic solver decisions,
costs, compatibility and BOM outputs are unchanged and remain the authority.

## Reproducible fresh migration gate

The checked-in helper creates a randomly named empty PostgreSQL database, upgrades
it through `0010_manual_inventory`, validates pgvector/history triggers/C08 audit and
scenario schema,
then drops only that isolated database:

```powershell
docker compose cp apps/api/tests/compose_rag_migration.py api:/tmp/c08_migration.py
docker compose exec -T api python /tmp/c08_migration.py
```
