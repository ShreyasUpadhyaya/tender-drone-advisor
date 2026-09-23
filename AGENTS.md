# Engineering protocol

## Approval gate

Only one planned implementation commit may be active. Start C01 only after `approve C01`. At its end, make its one commit, run its acceptance checks, issue the required stop report, and stop. Do not begin C02 until `approve C02`; repeat through C08. `revise Cxx: ...` changes only that commit; `pause` stops work.

Never combine scopes, amend an approved commit, create speculative later commits, or implement product behavior during the planning step.

## Required stop report

Report commit SHA and exact message; changed files; commands and pass/fail evidence; acceptance-check and demo evidence; migration/deployment actions; known limitations, risks, blockers; and the single next approval command. Never claim an unrun check passed.

## Push gate

After each approved implementation commit, stop and report the commit hash, changed files, tests, local/preview URLs, and known gaps. Then ask exactly: `Ready to push Cxx?` Do not push until the owner explicitly replies `push Cxx`.

On `push Cxx`, create or use a GitHub repository named `tender-drone-advisor`, configure it as `origin`, and push that exact approved commit. Default the GitHub repository to private unless the owner explicitly asks for public. If GitHub authentication or repository creation is blocked, stop and report the exact user action required; never expose secrets.

After a successful push, stop again. Do not begin the next commit until the owner explicitly replies `approve C(next)`. Every later commit must be pushed before its successor begins.

## Engineering rules

- Use strict TypeScript and typed Python; use versioned Pydantic API contracts.
- Keep money in integer paise and physical quantities in canonical SI units; convert only at explicit API/UI boundaries.
- Preserve immutable document page/span anchors and document versions. Citations resolve to bounded visible excerpts.
- Version catalog records and solver results. Solver code is pure, deterministic, and independently tested.
- Treat LLM output as untrusted: strict JSON schema, exactly one repair attempt, then `needs_review`.
- LLMs cannot invent catalog facts, constraints, prices, certifications, or feasibility conclusions.
- UI/logs/traces must not expose prompts, API keys, secrets, or unrestricted tender text.
- CI uses fakes/fixtures for provider-dependent tests.

## Secrets policy

Copy `.env.example` to untracked `.env` only when needed locally. Use deployment secret stores. Never commit `.env`, keys, tokens, production credentials, or sensitive tender bodies. Redact these from logs and telemetry.

## Definition of done

Each commit contains only its scope, necessary migrations, risk-proportionate tests, and documented commands; UI changes include loading/empty/error states; workflow changes include safe traces. It is done only after the stop report and owner approval gate.
