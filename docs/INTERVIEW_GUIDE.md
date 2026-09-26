# Interview architecture guide

LangChain is used inside nodes for provider abstraction, prompt templates,
structured output and embedding adapters. LangGraph is used for durable state,
conditional review routing, bounded retry/repair loops, interruption/resume and
auditable node traces. A linear chain cannot make those state transitions clear.

C05 is deliberately separate: it consumes cited requirements and immutable
catalog snapshots, then deterministically applies compatibility, mass/power,
availability, price, costing and ranking rules. An LLM never selects a component,
changes a BOM quantity, calculates a total, or upgrades a provisional conclusion.

RAG/pgvector adds snapshot-filtered evidence retrieval across tender spans,
requirements, catalog descriptions and solver facts. It helps organize grounded
answers, but semantic similarity cannot bypass a mandatory constraint or turn a
missing requirement into feasibility. This separation is the core safety story.

For the demo, walk Upload → cited extraction → review/candidate state → C05
configuration and BOM → grounded report → collapsed LangGraph audit. Mention the
fake synthetic fixture, the human approval boundary, implementation controls and
remaining production integration work honestly.
