# Demo checklist

For the click-by-click regression covering review revisions, manual inventory ownership, future stock and scenario replay, use [MANUAL_TESTING.md](MANUAL_TESTING.md).

## Offline start

```powershell
$env:LLM_PROVIDER="fake"
$env:LLM_MODEL="fixture-v1"
$env:RAG_EMBEDDING_PROVIDER="fake"
$env:RAG_REPORT_PROVIDER="fake"
docker compose up --build -d
docker compose exec -T api alembic current
docker compose cp apps/api/tests/compose_rag_migration.py api:/tmp/c08_migration.py
docker compose exec -T api python /tmp/c08_migration.py
docker compose cp apps/api/tests/compose_c08_demo.py api:/tmp/c08_demo.py
docker compose cp apps/api/tests/fixtures/solver_catalog.json api:/tmp/c08_seed.json
docker compose cp apps/api/tests/fixtures/c08-feasible-tender.txt api:/tmp/c08-feasible-tender.txt
docker compose cp apps/api/tests/fixtures/c08-needs-review-tender.txt api:/tmp/c08-needs-review-tender.txt
docker compose exec -T api python /tmp/c08_demo.py --seed /tmp/c08_seed.json --feasible /tmp/c08-feasible-tender.txt --needs-review /tmp/c08-needs-review-tender.txt
```

Keep those overrides in the same PowerShell session used to start Compose. They
prevent an untracked live-provider `.env` from turning a synthetic rehearsal into
a paid call. Confirm only provider/model names in the containers; never print keys.

Open `http://localhost:3000`. The **Local Demo Mode** banner is expected. It is
not a production authorization claim.

The command is the reproducible, synthetic-only C08 end-to-end smoke. It proves
upload → ingestion → cited extraction → catalog → deterministic C05 analysis →
BOM → grounded report/citations/audit, then checks the separate needs-review
route. It requires `LLM_PROVIDER=fake`, `RAG_EMBEDDING_PROVIDER=fake` and
`RAG_REPORT_PROVIDER=fake`; these are the checked-in local defaults. Its final
JSON summary contains only counts/statuses and no tenant or tender content.
Pass `--analysis-date YYYY-MM-DD` to replay a particular deterministic analysis
identity; the default is the current date so a prior failed demo identity is not
silently reused.

## Feasible synthetic path

1. Upload `apps/api/tests/fixtures/c08-feasible-tender.txt`.
2. Wait for *Tender processed*, then start extraction. The explicitly labelled
   offline fixture produces four cited requirements through the fake adapter.
3. Verify each requirement’s source drawer. In **Analyze configurations**, use
   the Catalog snapshot control to select **Synthetic C05 V1 · Draft ·
   Synthetic demo**. This is the explicitly labelled C08 synthetic solver
   catalog imported by the command above; it is an immutable input to C05, not
   a frontend feasibility calculation. Use **Refresh catalog snapshots** if
   the browser was open before the seed import, then start analysis.
4. Show the deterministic configuration, BOM, INR paise-derived cost display,
   rejected incompatible near-miss, and grounded report/audit panel.
5. State that the result is decision support, not engineering/certification
   approval; C05 is the feasibility and cost authority.

## Needs-review path

1. Upload `apps/api/tests/fixtures/c08-needs-review-tender.txt`.
2. Start extraction; the ordinary fake path returns no invented requirements.
3. Show grouped clarifications, provisional catalog state and the blocking banner.
4. Expand Technical audit trail only for a technical reviewer.

## Contingency

If a worker is interrupted, use the documented idempotent request to recover;
for a deliberately stale job, an admin can call `/v1/ops/recovery/stale`.
Use `/v1/ops/readiness` for a dependency-safe status, not raw container logs.
Never upload a private tender in the rehearsal.
