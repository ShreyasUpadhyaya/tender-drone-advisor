"use client";

import {
  ChangeEvent,
  FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  api,
  type Analysis,
  type CandidateResponse,
  type CatalogCandidate,
  type Configuration,
  type CurrentCatalogVersion,
  type DocumentStatus,
  type Evidence,
  type ExtractionRun,
  type Requirement,
} from "../lib/api";
import {
  formatInr,
  formatRequirementValue,
  humanLabel,
  isTerminal,
  requiresReview,
  safeReason,
  statusLabel,
} from "../lib/presentation";

type Workspace = {
  document?: DocumentStatus;
  extraction?: ExtractionRun;
  analysis?: Analysis;
  configurations: Configuration[];
  report?: Record<string, unknown>;
};
type Issue = {
  id?: string;
  issue?: {
    code?: string;
    detail?: string;
    requirement_index?: number | null;
    blocking?: boolean;
  };
  state?: string;
};
const initial: Workspace = { configurations: [] };

function Badge({ value }: { value?: string | null }) {
  return (
    <span
      className={`badge badge-${(value ?? "unknown").replaceAll("_", "-")}`}
    >
      {statusLabel(value)}
    </span>
  );
}
function Alert({
  children,
  tone = "warning",
}: {
  children: React.ReactNode;
  tone?: "warning" | "info" | "danger";
}) {
  return (
    <aside className={`alert alert-${tone}`} role="status">
      {children}
    </aside>
  );
}
function TechnicalDetails({ children }: { children: React.ReactNode }) {
  return (
    <details className="technical-details">
      <summary>Technical details</summary>
      {children}
    </details>
  );
}
function SectionHeading({
  number,
  title,
  description,
}: {
  number?: string;
  title: string;
  description: string;
}) {
  return (
    <div className="section-heading">
      {number ? <span className="section-number">{number}</span> : null}
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    </div>
  );
}
function Status({
  title,
  status,
  details,
}: {
  title: string;
  status: string;
  details: string;
}) {
  return (
    <div className="status">
      <strong>{title}</strong>
      <Badge value={status} />
      <span>{details}</span>
    </div>
  );
}
function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export default function HomePage() {
  const [workspace, setWorkspace] = useState<Workspace>(initial);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [candidateResult, setCandidateResult] = useState<CandidateResponse>();
  const [catalog, setCatalog] = useState<CurrentCatalogVersion>();
  const [ragNodes, setRagNodes] = useState<Record<string, unknown>[]>([]);
  const [ragCitations, setRagCitations] = useState<Record<string, unknown>[]>(
    [],
  );
  const [selected, setSelected] = useState<Configuration>();
  const [citation, setCitation] = useState<
    Evidence | Record<string, unknown>
  >();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File>();
  const polling = useRef<ReturnType<typeof setTimeout> | null>(null);
  const update = (patch: Partial<Workspace>) =>
    setWorkspace((state) => ({ ...state, ...patch }));
  const critical =
    requiresReview(
      workspace.extraction?.state,
      workspace.extraction?.review_state,
    ) || workspace.analysis?.status === "needs_review";
  const extractedReady =
    workspace.extraction?.state === "completed" && !critical;

  useEffect(
    () => () => {
      if (polling.current) clearTimeout(polling.current);
    },
    [],
  );
  useEffect(() => {
    api
      .currentCatalog()
      .then(setCatalog)
      .catch(() => undefined);
  }, []);
  const poll = (fetcher: () => Promise<boolean>, attempt = 0) => {
    if (attempt > 10) return;
    polling.current = setTimeout(
      async () => {
        if (!(await fetcher())) poll(fetcher, attempt + 1);
      },
      Math.min(1000 * 2 ** attempt, 10000),
    );
  };
  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const document = await api.upload(file);
      update({
        document,
        extraction: undefined,
        analysis: undefined,
        configurations: [],
        report: undefined,
      });
      setRequirements([]);
      setIssues([]);
      setCandidateResult(undefined);
      setSelected(undefined);
      poll(async () => {
        const next = await api.document(document.document_id);
        update({ document: next });
        return isTerminal(next.state);
      });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  async function extract() {
    if (!workspace.document || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const extraction = await api.startExtraction(
        workspace.document.document_version_id,
      );
      update({ extraction });
      poll(async () => {
        const run = await api.extraction(extraction.trace_id);
        update({ extraction: run });
        if (isTerminal(run.state)) {
          setRequirements(await api.requirements(run.trace_id));
          setIssues((await api.extractionIssues(run.trace_id)) as Issue[]);
        }
        return isTerminal(run.state);
      });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  async function retrieveCandidates() {
    if (!workspace.extraction || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      setCandidateResult(
        await api.retrieveCandidates(workspace.extraction.trace_id),
      );
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  async function analyse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace.extraction || busy) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(undefined);
    try {
      const analysis = await api.startAnalysis(
        workspace.extraction.trace_id,
        String(data.get("catalogVersion")),
        String(data.get("analysisDate")),
      );
      update({ analysis });
      poll(async () => {
        const run = await api.analysis(analysis.id);
        update({ analysis: run });
        if (isTerminal(run.state))
          update({
            configurations: (await api.configurations(run.id)).configurations,
          });
        return isTerminal(run.state);
      });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  async function openConfiguration(config: Configuration) {
    if (!workspace.analysis) return;
    try {
      setSelected(await api.configuration(workspace.analysis.id, config.id));
    } catch (caught) {
      setError(message(caught));
    }
  }
  async function startReport() {
    if (!workspace.analysis || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const job = await api.startReport(workspace.analysis.id);
      poll(async () => {
        const run = await api.ragRun(job.id);
        if (isTerminal(run.state)) {
          update({ report: await api.report(job.id) });
          setRagNodes((await api.ragNodes(job.id)).nodes);
          setRagCitations((await api.ragCitations(job.id)).citations);
        }
        return isTerminal(run.state);
      });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  const reviewCount = issues.filter(
    (issue) => issue.issue?.blocking !== false,
  ).length;
  return (
    <main className="shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Tender Drone Advisor</p>
          <h1>Decision workspace</h1>
          <p className="lede">
            Turn a tender into a cited, reviewable build decision—without hiding
            uncertainty.
          </p>
        </div>
        <Badge
          value={
            workspace.analysis?.status ??
            workspace.extraction?.state ??
            workspace.document?.state
          }
        />
      </header>
      <aside
        className="demo-banner"
        aria-label="Local Demo Mode security boundary"
      >
        <strong>Local Demo Mode</strong>
        <span>
          This prototype is intentionally open for local demonstrations.
          Production hardening adds authentication, role-based access, tenant
          isolation, signed uploads, and per-user audit attribution.
        </span>
      </aside>
      <Journey
        document={workspace.document}
        extraction={workspace.extraction}
        analysis={workspace.analysis}
        reviewBlocked={critical}
      />
      {error && (
        <Alert tone="danger">
          <strong>We could not complete that step.</strong> {error}{" "}
          <button className="quiet-button" onClick={() => setError(undefined)}>
            Dismiss
          </button>
        </Alert>
      )}
      {critical && (
        <Alert>
          <strong>Needs review — no configuration is approved.</strong>{" "}
          {reviewCount
            ? `${reviewCount} clarification${reviewCount === 1 ? "" : "s"} still affect a safe build decision.`
            : "Resolve missing, ambiguous, unsupported, or low-confidence requirements before engineering sign-off."}
        </Alert>
      )}

      <section className="card section-card" id="upload">
        <SectionHeading
          number="1"
          title="Upload tender"
          description="Add the PDF, DOCX, or TXT tender you want to assess."
        />
        <form onSubmit={upload} className="upload-form">
          <label>
            Choose tender file
            <input
              aria-label="Tender file"
              type="file"
              accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
              onChange={(event: ChangeEvent<HTMLInputElement>) =>
                setFile(event.target.files?.[0])
              }
            />
          </label>
          <button disabled={!file || busy}>
            {busy ? "Working…" : "Upload tender"}
          </button>
        </form>
        {workspace.document && (
          <Status
            title="Tender processing"
            status={workspace.document.state}
            details={
              workspace.document.state === "completed"
                ? `${workspace.document.source_span_count} source sections are ready to review.`
                : "The document is being prepared with page-level evidence."
            }
          />
        )}
      </section>
      <section className="card section-card" id="requirements">
        <SectionHeading
          number="2"
          title="Review requirements"
          description="Extract the tender requirements, then verify the evidence before matching a catalog."
        />
        <button
          disabled={
            !workspace.document ||
            workspace.document.state !== "completed" ||
            busy
          }
          onClick={extract}
        >
          Extract requirements
        </button>
        {workspace.extraction && (
          <ExtractionSummary
            extraction={workspace.extraction}
            requirementCount={requirements.length}
            issueCount={reviewCount}
          />
        )}
      </section>
      <Requirements
        requirements={requirements}
        issues={issues}
        onCitation={setCitation}
      />

      <section className="card section-card" id="catalog">
        <SectionHeading
          number="4"
          title="Match catalog"
          description="Find available, compatible candidates. Matching remains provisional until key tender details are verified."
        />
        {critical && (
          <ProvisionalNotice
            missing={candidateResult?.missing_critical_categories ?? []}
          />
        )}
        <button
          disabled={
            !workspace.extraction ||
            !isTerminal(workspace.extraction.state) ||
            busy
          }
          onClick={retrieveCandidates}
        >
          {candidateResult ? "Refresh catalog matches" : "Match catalog"}
        </button>
        <Candidates result={candidateResult} blocked={critical} />
      </section>
      <section className="card section-card" id="analysis">
        <SectionHeading
          number="5"
          title="Analyze configurations"
          description="The deterministic solver evaluates constraints, availability, lead time, BOM and cost."
        />
        {critical && (
          <BlockedStep reason="Resolve clarification items before a build decision can be presented as final." />
        )}
        <form onSubmit={analyse} className="form-row">
          <label>
            Catalog snapshot
            <span className="catalog-selection">
              {catalog
                ? `${catalog.version} · ${humanLabel(catalog.status)}`
                : "No current catalog snapshot is configured"}
            </span>
            <input
              name="catalogVersion"
              type="hidden"
              value={catalog?.id ?? ""}
              readOnly
            />
          </label>
          <label>
            Analysis date
            <input
              name="analysisDate"
              required
              type="date"
              defaultValue={new Date().toISOString().slice(0, 10)}
            />
          </label>
          <button disabled={!extractedReady || !catalog || busy}>
            Analyze configurations
          </button>
        </form>
        {workspace.analysis && (
          <Status
            title="Configuration analysis"
            status={workspace.analysis.state}
            details={`${workspace.analysis.summary?.configuration_count ?? 0} option${workspace.analysis.summary?.configuration_count === 1 ? "" : "s"} evaluated · ${humanLabel(workspace.analysis.outcome ?? "pending")}`}
          />
        )}
        <TechnicalDetails>
          <p>
            Catalog snapshot and analysis identifiers are retained with the
            immutable analysis for reproducibility.
          </p>
        </TechnicalDetails>
      </section>
      <Comparison
        configurations={workspace.configurations}
        onOpen={openConfiguration}
      />
      {selected && (
        <ConfigurationDetail
          configuration={selected}
          onCitation={setCitation}
        />
      )}
      <section className="card section-card" id="recommendation">
        <SectionHeading
          number="7"
          title="Read recommendation"
          description="A grounded report organizes solver-backed facts and tender evidence; it cannot grant approval."
        />
        <button
          onClick={startReport}
          disabled={
            !workspace.analysis ||
            workspace.analysis.state !== "completed" ||
            busy
          }
        >
          Generate grounded report
        </button>
        {workspace.report ? (
          <Report report={workspace.report} onCitation={setCitation} />
        ) : (
          <EmptyState
            title="No report yet"
            detail="After analysis completes, create a cited summary for the decision team."
          />
        )}
      </section>
      <TechnicalAudit
        nodes={ragNodes}
        citations={ragCitations}
        onCitation={setCitation}
      />
      <section className="card section-card export-card">
        <SectionHeading
          title="Export decision brief"
          description="Exports use human labels and include the tender summary, review blockers, configuration facts, BOM, costs and evidence appendix."
        />
        <p className="muted">
          {critical
            ? "This export is stamped Needs review / not approved."
            : "Export remains a decision-support artifact; engineering and commercial approval stay human-led."}
        </p>
        <button
          onClick={() =>
            downloadBrief(workspace, requirements, issues, selected, "csv")
          }
          disabled={!workspace.document}
        >
          Export decision brief CSV
        </button>
        <button
          className="secondary-button"
          onClick={() =>
            downloadBrief(workspace, requirements, issues, selected, "print")
          }
          disabled={!workspace.document}
        >
          Print decision brief
        </button>
      </section>
      {citation && (
        <CitationDrawer
          citation={citation}
          close={() => setCitation(undefined)}
        />
      )}
      <footer>
        Local/demo boundary: do not expose this workspace to untrusted networks.
        Tender excerpts are rendered as text, API keys never enter the browser,
        and costs are displayed from backend paise values without client-side
        recalculation.
      </footer>
    </main>
  );
}

function BlockedStep({ reason }: { reason: string }) {
  return (
    <div className="blocked-step">
      <strong>Waiting for clarification</strong>
      <span>{reason}</span>
    </div>
  );
}
function Journey({
  document,
  extraction,
  analysis,
  reviewBlocked,
}: {
  document?: DocumentStatus;
  extraction?: ExtractionRun;
  analysis?: Analysis;
  reviewBlocked: boolean;
}) {
  const steps = [
    ["Upload tender", Boolean(document)],
    ["Review requirements", Boolean(extraction)],
    ["Resolve clarifications", !reviewBlocked && Boolean(extraction)],
    ["Match catalog", !reviewBlocked && Boolean(extraction)],
    ["Analyze configurations", !reviewBlocked && Boolean(analysis)],
    ["Compare cost & BOM", !reviewBlocked && Boolean(analysis)],
    ["Read recommendation", !reviewBlocked && Boolean(analysis)],
  ];
  return (
    <nav className="journey" aria-label="Tender decision journey">
      {steps.map(([title, enabled], index) => (
        <div
          key={String(title)}
          className={`journey-step ${enabled ? "complete" : "waiting"}`}
        >
          <span>{index + 1}</span>
          <strong>{title}</strong>
          {!enabled && index > 1 && reviewBlocked ? (
            <small>Needs review</small>
          ) : null}
        </div>
      ))}
    </nav>
  );
}
function ExtractionSummary({
  extraction,
  requirementCount,
  issueCount,
}: {
  extraction: ExtractionRun;
  requirementCount: number;
  issueCount: number;
}) {
  const ready =
    extraction.state === "completed" &&
    !requiresReview(extraction.state, extraction.review_state);
  return (
    <div className="result-summary">
      <div>
        <Badge value={ready ? "completed" : extraction.state} />
        <strong>
          {ready
            ? "Tender processed"
            : extraction.state === "failed"
              ? "Extraction needs attention"
              : "Tender review in progress"}
        </strong>
        <p>
          {ready
            ? `${requirementCount} validated requirement${requirementCount === 1 ? "" : "s"} found with traceable tender evidence.`
            : issueCount
              ? `${issueCount} critical detail${issueCount === 1 ? " needs" : "s need"} clarification before a recommendation can be confirmed.`
              : "We are validating extracted values and tender evidence."}
        </p>
      </div>
      <TechnicalDetails>
        <p>Technical audit ID: {extraction.trace_id}</p>
        <ol className="audit-list">
          {extraction.nodes.map((node) => (
            <li key={`${node.node}-${node.attempt}`}>
              <strong>{humanLabel(node.node)}</strong>
              <Badge value={node.status} />
              {node.elapsed_ms !== undefined ? (
                <span>{node.elapsed_ms} ms</span>
              ) : null}
            </li>
          ))}
        </ol>
      </TechnicalDetails>
    </div>
  );
}

function Requirements({
  requirements,
  issues,
  onCitation,
}: {
  requirements: Requirement[];
  issues: Issue[];
  onCitation: (value: Evidence | Record<string, unknown>) => void;
}) {
  const [filter, setFilter] = useState("all");
  const filters = [
    "all",
    "flight",
    "payload",
    "communications",
    "compliance",
    "commercial",
    "mandatory",
    "review",
    "low",
  ];
  const visible = requirements.filter(({ requirement }) => {
    if (filter === "all") return true;
    if (filter === "mandatory") return requirement.semantics === "mandatory";
    if (filter === "low") return requirement.confidence < 0.7;
    if (filter === "review") return requirement.semantics === "ambiguous";
    if (filter === "flight")
      return [
        "platform",
        "range",
        "endurance",
        "altitude",
        "speed",
        "mtow",
        "environment",
        "wind_tolerance",
        "temperature",
      ].includes(requirement.category);
    if (filter === "payload")
      return ["payload", "sensors", "camera"].includes(requirement.category);
    if (filter === "communications")
      return ["communication", "navigation", "integration"].includes(
        requirement.category,
      );
    if (filter === "commercial")
      return [
        "procurement",
        "quantity",
        "delivery",
        "warranty",
        "commercial",
      ].includes(requirement.category);
    return requirement.category === filter;
  });
  const grouped = useMemo(
    () => groupIssues(issues, requirements),
    [issues, requirements],
  );
  return (
    <section className="card section-card" id="review">
      <SectionHeading
        number="3"
        title="Resolve clarifications"
        description="Verify each extracted tender clause and address anything that could change the build decision."
      />
      <div className="filter-row" role="group" aria-label="Requirement filters">
        {filters.map((item) => (
          <button
            key={item}
            className={`filter-button ${filter === item ? "active" : ""}`}
            onClick={() => setFilter(item)}
            aria-pressed={filter === item}
          >
            {item === "all"
              ? "All requirements"
              : item === "low"
                ? "Low confidence"
                : item === "review"
                  ? "Needs review"
                  : humanLabel(item)}
          </button>
        ))}
      </div>
      {requirements.length === 0 ? (
        <EmptyState
          title="Requirements will appear here"
          detail="Finish extraction to review values, confidence and source pages."
        />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Requirement</th>
                  <th>What the tender says</th>
                  <th>Priority</th>
                  <th>Confidence</th>
                  <th>Evidence</th>
                  <th>Validation</th>
                </tr>
              </thead>
              <tbody>
                {visible.map(({ id, requirement }) => {
                  const evidence = requirement.validated_evidence[0];
                  return (
                    <tr key={id}>
                      <td>
                        <strong>{humanLabel(requirement.attribute)}</strong>
                        <small className="requirement-category">
                          {humanLabel(requirement.category)}
                        </small>
                      </td>
                      <td>
                        {formatRequirementValue(
                          requirement.original_value,
                          requirement.original_unit,
                        )}
                      </td>
                      <td>
                        <Badge value={requirement.semantics} />
                      </td>
                      <td>{Math.round(requirement.confidence * 100)}%</td>
                      <td>
                        {evidence ? (
                          <button
                            className="link-button"
                            onClick={() => onCitation(evidence)}
                          >
                            View source
                            <span className="sr-only">
                              {" "}
                              for {humanLabel(requirement.attribute)}
                            </span>
                          </button>
                        ) : (
                          "Evidence unavailable"
                        )}
                        <small>
                          {evidence?.page_number
                            ? `Page ${evidence.page_number}${evidence.section_name ? ` · ${evidence.section_name}` : ""}`
                            : "No page recorded"}
                        </small>
                      </td>
                      <td>
                        <Badge
                          value={evidence ? "validated" : "needs_review"}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <TechnicalDetails>
            <p>
              Internal requirement identifiers are preserved for auditability
              and intentionally hidden from the decision view.
            </p>
          </TechnicalDetails>
        </>
      )}
      <h3>Review blockers</h3>
      {grouped.length ? (
        <div className="issue-grid">
          {grouped.map((group) => (
            <article className="issue-card" key={group.key}>
              <div>
                <Badge
                  value={group.blocking ? "needs_review" : "informational"}
                />
                <h4>{group.title}</h4>
              </div>
              <p>{group.summary}</p>
              <dl>
                <div>
                  <dt>Why this matters</dt>
                  <dd>{group.why}</dd>
                </div>
                <div>
                  <dt>Affected requirements</dt>
                  <dd>{group.affected.join(", ")}</dd>
                </div>
                <div>
                  <dt>Recommended action</dt>
                  <dd>{group.action}</dd>
                </div>
              </dl>
              {group.evidence ? (
                <button
                  className="link-button"
                  onClick={() => onCitation(group.evidence!)}
                >
                  View tender evidence
                </button>
              ) : (
                <span className="muted">
                  No source excerpt is available for this review item.
                </span>
              )}
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No review blockers loaded"
          detail="Validated issues and clarification actions will appear here when extraction finishes."
        />
      )}
    </section>
  );
}
function groupIssues(issues: Issue[], requirements: Requirement[]) {
  const groups = new Map<
    string,
    {
      key: string;
      title: string;
      summary: string;
      why: string;
      action: string;
      affected: string[];
      evidence?: Evidence;
      blocking: boolean;
    }
  >();
  for (const record of issues) {
    const issue = record.issue ?? {};
    const requirement =
      issue.requirement_index == null
        ? undefined
        : requirements[issue.requirement_index];
    const title = issue.code
      ? humanLabel(issue.code)
      : "Tender detail needs clarification";
    const key = `${issue.code ?? "review"}-${requirement?.requirement.category ?? "general"}`;
    const current = groups.get(key) ?? {
      key,
      title,
      summary: issue.detail
        ? humanizeIssueSummary(issue.detail)
        : "This tender detail could not be verified.",
      why: "The system cannot safely confirm a build decision without verified tender evidence.",
      action: actionForIssue(issue.code, requirement),
      affected: [],
      evidence: requirement?.requirement.validated_evidence[0],
      blocking: issue.blocking !== false,
    };
    const label = requirement
      ? humanLabel(requirement.requirement.attribute)
      : categoryFromDetail(issue.detail);
    if (label && !current.affected.includes(label))
      current.affected.push(label);
    groups.set(key, current);
  }
  return [...groups.values()];
}
function humanizeIssueSummary(value: string) {
  const summary = safeReason(value)
    .replace(/^review\s+/i, "")
    .replace(/^missing\s+([^.;]+)([.;])/i, "$1 is missing$2")
    .replace(/\bno value inferred\b/gi, "no value was inferred");
  return `${summary.slice(0, 1).toUpperCase()}${summary.slice(1).toLowerCase()}`;
}
function categoryFromDetail(detail?: string) {
  return detail?.match(/range|endurance|payload|altitude|speed/i)?.[0]
    ? humanLabel(detail.match(/range|endurance|payload|altitude|speed/i)?.[0])
    : "Tender requirement";
}
function actionForIssue(code?: string, requirement?: Requirement) {
  if (code?.includes("evidence"))
    return "Confirm the source clause and re-run extraction if the evidence is incomplete.";
  if (code?.includes("unit") || code?.includes("normal"))
    return "Ask the buyer to state the value and unit explicitly.";
  return `Ask the buyer to provide a clear ${requirement ? humanLabel(requirement.requirement.attribute).toLowerCase() : "requirement"} value.`;
}

function ProvisionalNotice({ missing }: { missing: string[] }) {
  return (
    <div className="provisional-notice">
      <strong>Configuration matching is provisional</strong>
      <p>
        We cannot confirm a build recommendation because key flight requirements
        are missing or unverified.
      </p>
      <div>
        <span>
          Can evaluate now: catalog availability, documented compatibility and
          indicative cost.
        </span>
        <span>
          Need to clarify:{" "}
          {missing.length
            ? missing.map(humanLabel).join(", ")
            : "range, endurance and payload capacity"}
          .
        </span>
      </div>
    </div>
  );
}
function Candidates({
  result,
  blocked,
}: {
  result?: CandidateResponse;
  blocked: boolean;
}) {
  const [detail, setDetail] = useState<CatalogCandidate>();
  if (!result)
    return (
      <EmptyState
        title="Catalog matching has not started"
        detail="Use Match catalog to see candidates and clear explanations."
      />
    );
  return (
    <>
      <div className="candidate-summary">
        <span>
          {result.candidates.length} candidate
          {result.candidates.length === 1 ? "" : "s"} evaluated
        </span>
        {result.missing_requirements.length ? (
          <span>
            Clarifications needed:{" "}
            {result.missing_requirements.slice(0, 3).map(humanLabel).join(", ")}
          </span>
        ) : null}
      </div>
      <div className="candidate-grid">
        {result.candidates.map((candidate) => (
          <CandidateCard
            key={candidate.item.id}
            candidate={candidate}
            blocked={blocked}
            onDetail={() => setDetail(candidate)}
          />
        ))}
      </div>
      {detail ? (
        <CandidateDrawer
          candidate={detail}
          close={() => setDetail(undefined)}
        />
      ) : null}
    </>
  );
}
function CandidateCard({
  candidate,
  blocked,
  onDetail,
}: {
  candidate: CatalogCandidate;
  blocked: boolean;
  onDetail: () => void;
}) {
  const item = candidate.item;
  const unavailable =
    item.availability === "unavailable" ||
    item.lifecycle_status === "deprecated";
  const status = unavailable
    ? "unavailable"
    : candidate.eligible
      ? blocked
        ? "partial"
        : "matches"
      : "rejected";
  const reasons = candidate.breakdown.map(candidateReason).slice(0, 3);
  return (
    <article className="candidate-card">
      <div className="candidate-heading">
        <div>
          <Badge value={status} />
          <h3>{item.name}</h3>
          <p>
            {humanLabel(item.category)} · {item.sku}
          </p>
        </div>
        <strong>{formatInr(item.cost_paise)}</strong>
      </div>
      <dl className="candidate-facts">
        <div>
          <dt>Availability</dt>
          <dd>{humanLabel(item.availability)}</dd>
        </div>
        <div>
          <dt>Weight</dt>
          <dd>
            {item.weight_grams != null
              ? `${item.weight_grams} g`
              : "Not recorded"}
          </dd>
        </div>
        <div>
          <dt>Lead time</dt>
          <dd>{item.lead_time_days} days</dd>
        </div>
      </dl>
      <p className="candidate-reasons">
        {reasons.length ? reasons.join(" · ") : "No decision detail returned."}
      </p>
      {candidate.penalties.length ? (
        <small>
          Trade-off:{" "}
          {candidate.penalties
            .map(
              (penalty) =>
                `${humanLabel(penalty.attribute)} adds ${formatInr(penalty.cost_paise)} / ${Math.round(penalty.weight_kg * 1000)} g`,
            )
            .join("; ")}
        </small>
      ) : null}
      <button className="secondary-button" onClick={onDetail}>
        View details
      </button>
    </article>
  );
}
function candidateReason(value: string) {
  const [prefix, detail = value] = value.split(/:\s*/, 2);
  const attribute = prefix.includes(".")
    ? prefix.slice(prefix.lastIndexOf(".") + 1)
    : prefix;
  const label = humanLabel(attribute);

  if (/^missing\b/i.test(detail))
    return `${label} cannot yet be verified from the tender.`;
  if (/^type mismatch\b/i.test(detail))
    return `${label} needs a compatible value in the tender.`;
  if (/^matched$/i.test(detail))
    return `${label} matches the tender requirement.`;
  return `${label}: ${safeReason(detail)}.`;
}
function CandidateDrawer({
  candidate,
  close,
}: {
  candidate: CatalogCandidate;
  close: () => void;
}) {
  return (
    <div
      className="drawer"
      role="dialog"
      aria-modal="true"
      aria-label="Catalog candidate details"
    >
      <div>
        <button autoFocus className="quiet-button" onClick={close}>
          Close details
        </button>
        <Badge value={candidate.eligible ? "matches" : "rejected"} />
        <h2>{candidate.item.name}</h2>
        <p>
          {humanLabel(candidate.item.category)} · {candidate.item.sku}
        </p>
        <h3>Technical reasons</h3>
        <ul>
          {candidate.breakdown.map((reason) => (
            <li key={reason}>{candidateReason(reason)}</li>
          ))}
        </ul>
        <h3>Requirements not yet verifiable</h3>
        <p>
          {candidate.eligible
            ? "Any unresolved tender review items still need human confirmation."
            : "See the technical reasons above before treating this item as suitable."}
        </p>
        <TechnicalDetails>
          <p>Catalog record: {candidate.item.id}</p>
        </TechnicalDetails>
      </div>
    </div>
  );
}

function Comparison({
  configurations,
  onOpen,
}: {
  configurations: Configuration[];
  onOpen: (config: Configuration) => void;
}) {
  return (
    <section className="card section-card" id="compare">
      <SectionHeading
        number="6"
        title="Compare cost and BOM"
        description="Compare only solver-evaluated configurations; the backend remains the cost and feasibility authority."
      />
      {configurations.length === 0 ? (
        <EmptyState
          title="No configurations to compare"
          detail="Complete a deterministic analysis after review blockers are resolved."
        />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Platform</th>
                <th>Weight / payload</th>
                <th>Material / total</th>
                <th>Lead time</th>
                <th>Evidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {configurations.map((configuration) => (
                <tr key={configuration.id}>
                  <td>
                    <Badge value={configuration.status} />
                    <small>
                      {configuration.labels?.join(", ") ||
                        "No recommendation label"}
                    </small>
                  </td>
                  <td>Selected platform</td>
                  <td>
                    {configuration.total_weight_g} g / {configuration.payload_g}{" "}
                    g
                  </td>
                  <td>
                    {formatInr(configuration.cost.material_paise)}
                    <small>
                      {formatInr(configuration.cost.total_paise)} total
                    </small>
                  </td>
                  <td>
                    {configuration.lead_time_days} days ·{" "}
                    {configuration.immediately_buildable
                      ? "Available"
                      : "Not immediately buildable"}
                  </td>
                  <td>
                    {configuration.evaluations?.some(
                      (evaluation) =>
                        evaluation.requirement.validated_evidence?.length,
                    )
                      ? "Cited"
                      : "Needs evidence"}
                  </td>
                  <td>
                    <button
                      className="secondary-button"
                      onClick={() => onOpen(configuration)}
                    >
                      View BOM
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
function ConfigurationDetail({
  configuration,
  onCitation,
}: {
  configuration: Configuration;
  onCitation: (value: Evidence | Record<string, unknown>) => void;
}) {
  return (
    <section className="card section-card" id="bom">
      <SectionHeading
        title="BOM, cost and coverage"
        description="All figures are backend-provided and tied to the immutable catalog snapshot and solver policy."
      />
      <Alert>
        <Badge value={configuration.status} />{" "}
        {configuration.issues
          ?.map((issue) => safeReason(issue.detail))
          .join(" ") ||
          "Provisional results still require engineering certification."}
      </Alert>
      <h3>Bill of materials</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Component</th>
              <th>Quantity</th>
              <th>Unit cost</th>
              <th>Subtotal</th>
              <th>Inventory</th>
            </tr>
          </thead>
          <tbody>
            {configuration.bom.map((line) => (
              <tr key={line.item_id}>
                <td>{line.name}</td>
                <td>{line.quantity}</td>
                <td>{formatInr(line.unit_cost_paise)}</td>
                <td>{formatInr(line.subtotal_paise)}</td>
                <td>{line.inventory_qty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Cost breakdown</h3>
      <dl className="cost">
        {[
          ["Material", configuration.cost.material_paise],
          [
            "Engineering / integration",
            configuration.cost.engineering_integration_paise,
          ],
          ["Labour", configuration.cost.labour_paise],
          ["Overhead", configuration.cost.overhead_paise],
          ["Contingency", configuration.cost.contingency_paise],
          ["Tax", configuration.cost.tax_paise],
          ["Margin", configuration.cost.margin_paise],
          ["Total", configuration.cost.total_paise],
        ].map(([name, value]) => (
          <div key={String(name)}>
            <dt>{name}</dt>
            <dd>{formatInr(value as number | null)}</dd>
          </div>
        ))}
      </dl>
      <h3>
        Requirement coverage ({configuration.requirement_coverage_bps / 100}%)
      </h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Requirement</th>
              <th>Result</th>
              <th>Explanation</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {configuration.evaluations.map((evaluation) => (
              <tr key={evaluation.requirement_id}>
                <td>
                  {humanLabel(evaluation.requirement.attribute)}
                  <small>{humanLabel(evaluation.requirement.category)}</small>
                </td>
                <td>
                  <Badge value={evaluation.result} />
                </td>
                <td>{safeReason(evaluation.explanation)}</td>
                <td>
                  {evaluation.requirement.validated_evidence[0] ? (
                    <button
                      className="link-button"
                      onClick={() =>
                        onCitation(evaluation.requirement.validated_evidence[0])
                      }
                    >
                      View source
                    </button>
                  ) : (
                    "No citation"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TechnicalDetails>
        <p>Configuration ID: {configuration.id}</p>
        <p>Platform reference: {configuration.platform_id}</p>
      </TechnicalDetails>
    </section>
  );
}
function Report({
  report,
  onCitation,
}: {
  report: Record<string, unknown>;
  onCitation: (value: Evidence | Record<string, unknown>) => void;
}) {
  const sections = (report.sections ?? []) as {
    title: string;
    facts: { id: string; text: string; citation: Evidence }[];
  }[];
  return (
    <div className="report">
      <Alert>
        <strong>
          {String(report.approval ?? "Not approved") === "approved"
            ? "Decision support only"
            : "Not approved"}
        </strong>{" "}
        · Solver status: {humanLabel(String(report.solver_status ?? "pending"))}
      </Alert>
      {sections.map((section) => (
        <article className="report-section" key={section.title}>
          <h3>{section.title}</h3>
          {section.facts.map((fact) => (
            <p key={fact.id}>
              {fact.text}{" "}
              <button
                className="link-button"
                onClick={() => onCitation(fact.citation)}
              >
                View source
              </button>
            </p>
          ))}
        </article>
      ))}
      <h3>Clarification questions</h3>
      <ul>
        {((report.clarification_questions ?? []) as string[]).map(
          (question) => (
            <li key={question}>{question}</li>
          ),
        )}
      </ul>
    </div>
  );
}
function TechnicalAudit({
  nodes,
  citations,
  onCitation,
}: {
  nodes: Record<string, unknown>[];
  citations: Record<string, unknown>[];
  onCitation: (value: Evidence | Record<string, unknown>) => void;
}) {
  return (
    <section className="card section-card">
      <SectionHeading
        number="Audit"
        title="Technical audit trail"
        description="Safe execution records support technical review without exposing prompts, secrets or full tender text."
      />
      <details className="audit-details">
        <summary>Open technical audit trail</summary>
        {nodes.length ? (
          <ol className="audit-list">
            {nodes.map((node, index) => (
              <li key={String(node.node ?? index)}>
                <strong>
                  {humanLabel(String(node.node ?? "workflow step"))}
                </strong>
                <Badge value={String(node.status ?? "unknown")} />
                <span>{Number(node.elapsed_ms ?? 0)} ms</span>
                {node.branch ? (
                  <small>
                    Selected path: {humanLabel(String(node.branch))}
                  </small>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">
            Workflow records appear after a grounded report is generated.
          </p>
        )}
        {citations.length ? (
          <div className="audit-citations">
            {citations.map((item, index) => (
              <button
                className="link-button"
                key={String(item.span_id ?? index)}
                onClick={() => onCitation(item)}
              >
                Open cited evidence {index + 1}
              </button>
            ))}
          </div>
        ) : null}
      </details>
    </section>
  );
}
function CitationDrawer({
  citation,
  close,
}: {
  citation: Evidence | Record<string, unknown>;
  close: () => void;
}) {
  const value = citation as Evidence;
  return (
    <div
      className="drawer"
      role="dialog"
      aria-modal="true"
      aria-label="Citation source span"
    >
      <div>
        <button autoFocus className="quiet-button" onClick={close}>
          Close source
        </button>
        <p className="eyebrow">Tender evidence</p>
        <h2>Source evidence</h2>
        <p>
          Page {String(value.page_number ?? "not recorded")}
          {value.section_name ? ` · ${value.section_name}` : ""}
        </p>
        <blockquote>
          {String(
            value.quote ??
              "This reference points to a bounded tender excerpt. The text is available through the source-span record.",
          )}
        </blockquote>
        <TechnicalDetails>
          <p>Source span ID: {String(value.span_id ?? "unavailable")}</p>
          <p>
            Document version:{" "}
            {String(value.document_version_id ?? "unavailable")}
          </p>
        </TechnicalDetails>
      </div>
    </div>
  );
}
function message(error: unknown) {
  return error instanceof ApiError
    ? error.status === 422
      ? "Check the entered value and try again."
      : error.status === 503
        ? "The local worker is unavailable. Retry shortly."
        : "The requested item is not available in this workflow state."
    : "The request could not be completed. Retry when the local API and worker are available.";
}
function downloadBrief(
  workspace: Workspace,
  requirements: Requirement[],
  issues: Issue[],
  configuration: Configuration | undefined,
  format: "csv" | "print",
) {
  if (format === "print") {
    window.print();
    return;
  }
  const configurationRows = configuration
    ? [
        ["Selected configuration", humanLabel(configuration.status)],
        ["Material cost", formatInr(configuration.cost.material_paise)],
        ["Total cost", formatInr(configuration.cost.total_paise)],
        ...configuration.bom.map((line) => [
          `BOM: ${line.name}`,
          `${line.quantity} × ${formatInr(line.unit_cost_paise)}`,
        ]),
      ]
    : [];
  const rows = [
    [
      "Tender decision brief",
      workspace.analysis?.status ??
        workspace.extraction?.state ??
        workspace.document?.state ??
        "Not started",
    ],
    [
      "Approval",
      requiresReview(
        workspace.extraction?.state,
        workspace.extraction?.review_state,
      )
        ? "Needs review — not approved"
        : "Decision support only",
    ],
    ...requirements.map((row) => [
      humanLabel(row.requirement.attribute),
      formatRequirementValue(
        row.requirement.original_value,
        row.requirement.original_unit,
      ),
    ]),
    ...issues.map((issue) => [
      "Review blocker",
      issue.issue?.detail ?? "Tender detail needs clarification",
    ]),
    ...configurationRows,
  ];
  const csv = rows
    .map((row) =>
      row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(","),
    )
    .join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  link.download = "tender-decision-brief.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}
