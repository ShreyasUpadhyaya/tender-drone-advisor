# Manual browser test — review, stock and scenarios

Use only the synthetic fixtures in `apps/api/tests/fixtures/`. Demo Mode identifies its simulated administrator as `local-demo-operator`; production identity enforcement requires the documented OIDC integration.

## Start clean

1. In PowerShell, set `$env:LLM_PROVIDER="fake"`, `$env:LLM_MODEL="fixture-v1"`, `$env:RAG_EMBEDDING_PROVIDER="fake"`, and `$env:RAG_REPORT_PROVIDER="fake"`. In that same window run `docker compose down`, then `docker compose up --build -d` from the repository root. These overrides prevent an untracked live-provider `.env` from making paid calls.
2. Run `docker compose ps` and wait for PostgreSQL, Redis, MinIO, API, worker and web.
3. Confirm the API and worker report the fake provider names without printing any key. Open `http://localhost:3000` and confirm the Local Demo Mode banner names `local-demo-operator`.
4. Open `http://localhost:8000/docs` separately and confirm the Inventory endpoints appear.

## A. Normal extraction and filters

1. Click **Choose tender file** and select `apps/api/tests/fixtures/c08-feasible-tender.txt`.
2. Click **Upload tender**; wait for Completed and a non-zero source count.
3. Click **Extract requirements** and wait for a terminal status.
4. Confirm accepted, rejected/unresolved and source-section counts are explicit.
5. Click every requirement filter: **All requirements**, **Flight**, **Payload**, **Communications**, **Compliance**, **Commercial**, **Mandatory**, **Needs review**, and **Low confidence**.
6. Confirm each filter changes the displayed rows/count or shows an explicit zero-match message.
7. Click **View source**; confirm page/section/quote appear and Escape closes the drawer.

## B. Record and revise a review decision

1. In Review blockers, expand **Record a review decision**.
2. Select **Mark unresolved**, enter a rationale and save.
3. Confirm **Current review decision** shows the action and `local-demo-operator`.
4. Expand **Change recorded decision**, select **Record documented assumption**, enter a new rationale and save.
5. Expand **Review decision history**. Confirm the first event is Superseded and the second Current; both retain actor and timestamp.
6. Confirm evidence-backed correction is disabled without source evidence and requires explicit value/unit when available.

## C. Tally stock on hand

1. Scroll to **Reconcile available stock** and confirm the admin-ownership notice.
2. Leave **Item is not yet in the validated catalog** unchecked.
3. Select a catalog item; enter the on-hand count, `0` expected later, location and physical-count rationale.
4. Optionally link an accepted requirement or missing category, then click **Record inventory**.
5. Confirm the card shows name/SKU, quantity, location, `local-demo-operator` and Catalog linked.
6. Expand **Update count or availability**, change the count, enter a reason and click **Save new inventory version**.
7. Expand **Inventory history** and confirm both versions remain.
8. Repeat the exact same submission and confirm no duplicate version is created.

## D. Future and unlisted stock

1. Record a catalog item with zero on hand, a positive expected quantity and future date. It must be backorder before that date, not immediate stock.
2. Check **Item is not yet in the validated catalog** and enter internal SKU, name, category, manufacturer, count, location and rationale.
3. Save and confirm Pending catalog and engineering validation.
4. Confirm the unlisted record is visible but disabled in the scenario inventory selector.

## E. Immutable scenario stock snapshot

1. Scroll to **Explore build scenarios**. Add an internal assumption only for a missing tender value, with number, unit and rationale.
2. Under **Manual inventory to snapshot for this scenario**, select catalog-linked records.
3. Choose Baseline, Cost-optimized or Performance-oriented and click **Create scenario estimate**.
4. Confirm the result names the inventory snapshot and still separates tender verification, engineering feasibility, outstanding assumptions and required bid/compliance review.
5. Click **View solver analysis** and inspect configuration, BOM, cost, weight, availability and blockers.
6. Create a newer inventory version, repeat the scenario and confirm it produces a new analysis identity while the old result replays its old snapshot.

## Negative and recovery tests

1. Upload a synthetic unsupported/empty tender: zero accepted requirements must be explicit with meaningful next actions.
2. Use the documented fake-provider failure mode: Retry extraction must preserve and link the failed run.
3. Submit expected stock without a date, a negative count, malformed date or empty rationale: expect readable validation and no record.
4. Verify by API test that a non-admin receives 403 and a different admin subject cannot override the owning admin’s record.
5. Confirm no default screen exposes UUIDs, raw prompts, raw API dumps or LangGraph node slugs.

Pass only when review and stock changes append history, unlisted stock never enters the solver, and scenario feasibility never appears as tender compliance, bid approval or flight certification.
