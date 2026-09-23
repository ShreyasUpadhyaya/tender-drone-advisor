# Model routing and escalation

## Fixed primary assignments

- **Terra:** C01 platform bootstrap, C02 document ingestion, C07 operations UI, C08 hardening/deployment.
- **Astra:** C03 requirement extraction and graph-state design, C05 deterministic constraint solver/costing, C06 RAG/LangGraph/evaluation.
- **Luna:** C04 catalog CRUD/import/validation. After C04, use Luna only for bounded fixtures, documentation, small tests, or UI polish.

The named model owns implementation reasoning and the stop report, but never overrides the one-commit approval protocol.

## Rationale

Terra owns integrated platform, UI, and deployment surfaces. Astra owns schema/graph semantics, solver correctness, retrieval grounding, and evaluation safety. Luna owns bounded CRUD/import work and later independently verifiable support. All must respect the architecture authority boundary: LLMs assist; catalog and solver decide technical/cost facts; humans approve.

## Escalation policy

Escalate to the delivery owner, rather than silently expanding scope, for: commercial policy; certification/engineering interpretation; missing catalog capability/price/availability/lead-time data; tender fixture authorization; external integration; model-provider account/key; or any deviation from C01–C08.

Within an approved commit, the owning model may make reversible choices that meet documented contracts and record assumptions in the stop report. Cross-commit risk may be documented but cannot trigger dependent work before `approve Cxx`.

No model may fetch a tender, use an API key, contact a supplier, submit a tender, alter production, or make engineering/commercial approval without separate explicit authorization.
