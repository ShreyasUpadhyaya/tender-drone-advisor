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
  type CatalogItem,
  type CatalogCandidate,
  type Configuration,
  type CurrentCatalogVersion,
  type DocumentStatus,
  type Evidence,
  type ExtractionRun,
  type ExtractionIssue,
  type InventoryRecord,
  type Requirement,
  type ReviewWorkspace,
  type Scenario,
  type Session,
  type SourceSpan,
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
type Issue = ExtractionIssue;
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
  number?: number;
  title: string;
  description: string;
}) {
  return (
    <div className="section-heading">
      {number !== undefined ? (
        <span className="section-number" aria-hidden="true">
          {number}
        </span>
      ) : null}
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
  const [reviewWorkspace, setReviewWorkspace] = useState<ReviewWorkspace>();
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [candidateResult, setCandidateResult] = useState<CandidateResponse>();
  const [catalog, setCatalog] = useState<CurrentCatalogVersion>();
  const [catalogVersions, setCatalogVersions] = useState<
    CurrentCatalogVersion[]
  >([]);
  const [selectedCatalogId, setSelectedCatalogId] = useState<string>();
  const [session, setSession] = useState<Session>();
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [inventory, setInventory] = useState<InventoryRecord[]>([]);
  const [assumptionUnits, setAssumptionUnits] = useState<
    Record<string, string[]>
  >({});
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
  const [scenarioPrefill, setScenarioPrefill] = useState<{
    category: string;
    nonce: number;
  }>();
  const polling = useRef<ReturnType<typeof setTimeout> | null>(null);
  const update = (patch: Partial<Workspace>) =>
    setWorkspace((state) => ({ ...state, ...patch }));
  const extractionNeedsReview = requiresReview(
    workspace.extraction?.state,
    workspace.extraction?.review_state,
  );
  const critical =
    extractionNeedsReview || workspace.analysis?.status === "needs_review";
  const extractedReady =
    workspace.extraction?.state === "completed" && !extractionNeedsReview;

  useEffect(
    () => () => {
      if (polling.current) clearTimeout(polling.current);
    },
    [],
  );
  async function loadCatalogVersions() {
    try {
      const [current, versions] = await Promise.all([
        api.currentCatalog(),
        api.catalogVersions(),
      ]);
      setCatalog(current);
      setCatalogVersions(versions);
      setSelectedCatalogId((existing) =>
        existing && versions.some((entry) => entry.id === existing)
          ? existing
          : current.id,
      );
    } catch {
      // The workflow can still explain that no selectable catalog is configured.
    }
  }
  useEffect(() => {
    void loadCatalogVersions();
    void Promise.all([
      api.session(),
      api.inventoryRecords(),
      api.assumptionUnits(),
    ])
      .then(([identity, records, unitResponse]) => {
        setSession(identity);
        setInventory(records);
        setAssumptionUnits(unitResponse.units);
      })
      .catch(() => undefined);
  }, []);
  const selectedCatalog =
    catalogVersions.find((entry) => entry.id === selectedCatalogId) ?? catalog;
  useEffect(() => {
    if (!selectedCatalog) return;
    setCatalogItems([]);
    void api
      .catalogItems(selectedCatalog.version)
      .then(setCatalogItems)
      .catch(() => setCatalogItems([]));
  }, [selectedCatalog]);
  function changeCatalog(nextCatalogId: string) {
    if (nextCatalogId === selectedCatalogId) return;
    if (polling.current) clearTimeout(polling.current);
    setSelectedCatalogId(nextCatalogId);
    setCandidateResult(undefined);
    setScenarios([]);
    setSelected(undefined);
    setRagNodes([]);
    setRagCitations([]);
    update({
      analysis: undefined,
      configurations: [],
      report: undefined,
    });
  }
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
      setReviewWorkspace(undefined);
      setScenarios([]);
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
          await loadReviewWorkspace(run.trace_id);
        }
        return isTerminal(run.state);
      });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  async function loadReviewWorkspace(runId: string) {
    const review = await api.reviewWorkspace(runId);
    setReviewWorkspace(review);
    setRequirements(review.accepted_requirements);
    setIssues(review.issues);
  }
  async function retryExtraction() {
    if (!workspace.extraction || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const extraction = await api.retryExtraction(
        workspace.extraction.trace_id,
      );
      update({ extraction });
      setReviewWorkspace(undefined);
      poll(async () => {
        const run = await api.extraction(extraction.trace_id);
        update({ extraction: run });
        if (isTerminal(run.state)) await loadReviewWorkspace(run.trace_id);
        return isTerminal(run.state);
      });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }
  async function retrieveCandidates() {
    if (!workspace.extraction || !selectedCatalog || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      setCandidateResult(
        await api.retrieveCandidates(
          workspace.extraction.trace_id,
          selectedCatalog.version,
        ),
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
  async function openScenarioAnalysis(analysis: Analysis) {
    setError(undefined);
    try {
      const current = await api.analysis(analysis.id);
      update({ analysis: current, configurations: [] });
      if (isTerminal(current.state)) {
        const configurations = await api.configurations(current.id);
        update({
          analysis: current,
          configurations: configurations.configurations,
        });
      } else {
        poll(async () => {
          const next = await api.analysis(current.id);
          update({ analysis: next });
          if (isTerminal(next.state)) {
            update({
              configurations: (await api.configurations(next.id))
                .configurations,
            });
          }
          return isTerminal(next.state);
        });
      }
      document.getElementById("analysis")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    } catch (caught) {
      setError(message(caught));
    }
  }
  function startAssumption(category: string) {
    setScenarioPrefill({ category, nonce: Date.now() });
    document.getElementById("scenarios")?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
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
          Signed in as {session?.subject ?? "local demo operator"}. This
          prototype grants a simulated admin role for local demonstrations.
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
          number={1}
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
          number={2}
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
      <BuildabilityReadiness
        review={reviewWorkspace}
        candidates={candidateResult}
        analysis={workspace.analysis}
        configurations={workspace.configurations}
        onMatch={() => void retrieveCandidates()}
      />
      <Requirements
        requirements={requirements}
        issues={issues}
        extraction={workspace.extraction}
        review={reviewWorkspace}
        onCitation={setCitation}
        onRetry={() => void retryExtraction()}
        onError={setError}
        document={workspace.document}
        onExploreAssumption={startAssumption}
        onReviewRecorded={() =>
          workspace.extraction &&
          void loadReviewWorkspace(workspace.extraction.trace_id)
        }
      />

      <InventoryWorkspace
        session={session}
        items={catalogItems}
        records={inventory}
        extraction={workspace.extraction}
        requirements={requirements}
        onChanged={(record) =>
          setInventory((current) => [
            record,
            ...current.filter((value) => value.id !== record.id),
          ])
        }
        onError={setError}
      />

      {workspace.extraction && selectedCatalog && (
        <Scenarios
          extraction={workspace.extraction}
          catalog={selectedCatalog}
          inventory={inventory}
          catalogItems={catalogItems}
          assumptionUnits={assumptionUnits}
          scenarios={scenarios}
          onCreated={(scenario) =>
            setScenarios((current) => [
              scenario,
              ...current.filter((item) => item.id !== scenario.id),
            ])
          }
          onOpenAnalysis={openScenarioAnalysis}
          onError={setError}
          prefill={scenarioPrefill}
        />
      )}

      <section className="card section-card" id="catalog">
        <SectionHeading
          number={4}
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
          {candidateResult
            ? "Refresh provisional catalog matches"
            : critical
              ? "Match catalog provisionally"
              : "Match catalog"}
        </button>
        <Candidates result={candidateResult} blocked={critical} />
      </section>
      <section className="card section-card" id="analysis">
        <SectionHeading
          number={5}
          title="Analyze configurations"
          description="The deterministic solver evaluates constraints, availability, lead time, BOM and cost."
        />
        {critical && (
          <BlockedStep reason="Resolve clarification items before a build decision can be presented as final." />
        )}
        <form onSubmit={analyse} className="form-row">
          <label>
            Catalog snapshot
            <select
              aria-label="Catalog snapshot"
              name="catalogVersion"
              value={selectedCatalog?.id ?? ""}
              onChange={(event) => changeCatalog(event.target.value)}
              disabled={!selectedCatalog || busy}
            >
              {!selectedCatalog && (
                <option>No catalog snapshot is configured</option>
              )}
              {catalogVersions.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {`${humanLabel(entry.version)} · ${humanLabel(entry.status)} · ${humanLabel(entry.source)}`}
                </option>
              ))}
            </select>
            {selectedCatalog && (
              <span className="catalog-selection">
                Immutable snapshot · {humanLabel(selectedCatalog.source)}
              </span>
            )}
          </label>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void loadCatalogVersions()}
            disabled={busy}
          >
            Refresh catalog snapshots
          </button>
          <label>
            Analysis date
            <input
              name="analysisDate"
              required
              type="date"
              defaultValue={new Date().toISOString().slice(0, 10)}
            />
          </label>
          <button disabled={!extractedReady || !selectedCatalog || busy}>
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
        {!workspace.analysis && workspace.extraction && selectedCatalog ? (
          <EmptyState
            title="No analysis has been run for this catalog"
            detail={`Analyze the accepted requirements against ${humanLabel(selectedCatalog.version)}; no re-extraction is required.`}
          />
        ) : null}
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
          number={7}
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
          <Report
            report={workspace.report}
            extraction={workspace.extraction}
            configurations={workspace.configurations}
            selected={selected}
            requirements={requirements}
            issues={issues}
            scenarios={scenarios}
            onCitation={setCitation}
          />
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
        report={workspace.report}
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
            downloadBrief(
              workspace,
              requirements,
              issues,
              scenarios,
              workspace.configurations,
              selected,
              "csv",
            )
          }
          disabled={!workspace.document}
        >
          Export decision brief CSV
        </button>
        <button
          className="secondary-button"
          onClick={() =>
            downloadBrief(
              workspace,
              requirements,
              issues,
              scenarios,
              workspace.configurations,
              selected,
              "print",
            )
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
  extraction,
  review,
  document,
  onCitation,
  onRetry,
  onError,
  onReviewRecorded,
  onExploreAssumption,
}: {
  requirements: Requirement[];
  issues: Issue[];
  extraction?: ExtractionRun;
  review?: ReviewWorkspace;
  document?: DocumentStatus;
  onCitation: (value: Evidence | Record<string, unknown>) => void;
  onRetry: () => void;
  onError: (message: string) => void;
  onReviewRecorded: () => void;
  onExploreAssumption: (category: string) => void;
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
  const [decision, setDecision] = useState("mark_unresolved");
  const [rationale, setRationale] = useState("");
  const [correctedValue, setCorrectedValue] = useState("");
  const [correctedUnit, setCorrectedUnit] = useState("");
  const [saving, setSaving] = useState(false);
  const evidenceDecision = [
    "accept_verified_extraction",
    "correct_transcription_or_normalization",
  ].includes(decision);
  const correctionDecision =
    decision === "correct_transcription_or_normalization";
  const submitDecision = async (
    issue?: Issue,
    requirement?: Requirement,
    evidence?: Evidence,
    assumptionCategory?: string,
  ) => {
    if (!extraction || !rationale.trim()) return;
    if (evidenceDecision && !evidence) {
      onError(
        "This decision needs a cited tender source. Mark it unresolved or inspect source evidence first.",
      );
      return;
    }
    if (
      correctionDecision &&
      (!correctedValue.trim() || !correctedUnit.trim())
    ) {
      onError(
        "Record a corrected value and unit with its cited tender source.",
      );
      return;
    }
    setSaving(true);
    try {
      const current = review?.events.find(
        (event) => event.issue_id === issue?.id && event.is_current,
      );
      await api.recordReviewDecision(extraction.trace_id, {
        action: decision,
        rationale,
        issue_id: issue?.id,
        requirement_id: requirement?.id,
        source_span_id: evidence?.span_id,
        supersedes_event_id: current?.id,
        before_value: requirement
          ? {
              value: requirement.requirement.original_value,
              unit: requirement.requirement.original_unit,
            }
          : undefined,
        after_value: correctionDecision
          ? { value: correctedValue, unit: correctedUnit }
          : undefined,
      });
      setRationale("");
      setCorrectedValue("");
      setCorrectedUnit("");
      onReviewRecorded();
      if (decision === "record_documented_assumption")
        onExploreAssumption(assumptionCategory ?? "range");
    } finally {
      setSaving(false);
    }
  };
  const extractionPending =
    extraction && ["queued", "processing"].includes(extraction.state);
  const hasNoAccepted = Boolean(
    review && review.counts.accepted === 0 && !extractionPending,
  );
  return (
    <section className="card section-card" id="review">
      <SectionHeading
        number={3}
        title="Resolve clarifications"
        description="Verify each extracted tender clause and address anything that could change the build decision."
      />
      {extraction ? (
        <div className="review-state-summary" role="status">
          <Badge value={extraction.state} />
          <strong>
            {extraction.state === "failed"
              ? "Extraction failed safely"
              : extractionPending
                ? "Extraction is in progress"
                : `${review?.counts.accepted ?? requirements.length} accepted tender requirement${(review?.counts.accepted ?? requirements.length) === 1 ? "" : "s"}`}
          </strong>
          <span>
            {review
              ? `${review.counts.rejected_or_unresolved} rejected or unresolved candidate${review.counts.rejected_or_unresolved === 1 ? "" : "s"}; ${review.counts.source_spans} source section${review.counts.source_spans === 1 ? "" : "s"} available.`
              : "Loading the review workspace and source evidence."}
          </span>
          {extraction.state === "failed" ? (
            <button className="secondary-button" onClick={onRetry}>
              Retry extraction
            </button>
          ) : null}
        </div>
      ) : (
        <div className="review-state-summary">
          <strong>Extraction has not started</strong>
          <span>
            Upload and complete ingestion, then select Extract requirements.
          </span>
        </div>
      )}
      {requirements.length > 0 && (
        <div
          className="filter-row"
          role="group"
          aria-label="Requirement filters"
        >
          {filters.map((item) => (
            <button
              key={item}
              className={`filter-button ${filter === item ? "active" : ""}`}
              onClick={() => setFilter(item)}
              aria-pressed={filter === item}
            >
              {item === "all"
                ? `All requirements (${requirements.length})`
                : item === "low"
                  ? "Low confidence"
                  : item === "review"
                    ? "Needs review"
                    : humanLabel(item)}
            </button>
          ))}
        </div>
      )}
      {!extraction ? (
        <EmptyState
          title="Start with extraction"
          detail="No tender requirements have been requested yet. Use Extract requirements after ingestion completes."
        />
      ) : extractionPending ? (
        <EmptyState
          title="Preparing review workspace"
          detail="The current extraction stage is still running; accepted and rejected candidates will appear when it reaches a terminal state."
        />
      ) : extraction.state === "failed" ? (
        <EmptyState
          title="Extraction did not complete"
          detail={`The failed stage is ${review?.failed_stage ? humanLabel(review.failed_stage) : "recorded in the technical audit"}. Retry creates a new linked run and keeps this attempt.`}
        />
      ) : hasNoAccepted ? (
        <div className="empty-state actionable-empty">
          <strong>No accepted tender requirements</strong>
          <span>
            This run completed with zero citation-valid requirements. It is not
            a successful tender review.
          </span>
          <span>{review?.next_action}</span>
          <div className="empty-state-actions">
            {document ? (
              <SourceSectionExplorer
                documentId={document.document_id}
                onCitation={onCitation}
                onError={onError}
              />
            ) : null}
            <button
              type="button"
              className="secondary-button"
              onClick={() => onExploreAssumption("range")}
            >
              Explore build scenarios
            </button>
          </div>
        </div>
      ) : requirements.length === 0 ? (
        <EmptyState
          title="Loading accepted requirements"
          detail="The extraction completed; refresh the review workspace if this message persists."
        />
      ) : visible.length === 0 ? (
        <EmptyState
          title={`No ${filter === "all" ? "requirements" : humanLabel(filter).toLowerCase() + " requirements"} match this filter`}
          detail={`Clear the ${humanLabel(filter)} filter to view all ${requirements.length} accepted tender requirements.`}
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
                        {requirement.normalized_value !== undefined && (
                          <small>
                            Normalized:{" "}
                            {formatRequirementValue(
                              requirement.normalized_value,
                              requirement.normalized_unit,
                            )}
                          </small>
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
                  Not specified in tender — this missing-information blocker has
                  no source excerpt to verify.
                </span>
              )}
              {group.assumptionCategory ? (
                <button
                  type="button"
                  className="secondary-button estimate-action"
                  onClick={() => onExploreAssumption(group.assumptionCategory!)}
                >
                  Set an internal{" "}
                  {humanLabel(group.assumptionCategory).toLowerCase()} estimate
                </button>
              ) : null}
              {review?.events.find(
                (event) =>
                  event.issue_id === group.issue.id && event.is_current,
              ) ? (
                <div className="current-decision">
                  <strong>Current review decision</strong>
                  <span>
                    {humanLabel(
                      review.events.find(
                        (event) =>
                          event.issue_id === group.issue.id && event.is_current,
                      )!.action,
                    )}
                  </span>
                  <small>
                    Recorded by{" "}
                    {
                      review.events.find(
                        (event) =>
                          event.issue_id === group.issue.id && event.is_current,
                      )!.reviewer
                    }
                  </small>
                </div>
              ) : null}
              {extraction ? (
                <details className="review-decision">
                  <summary>
                    {review?.events.some(
                      (event) =>
                        event.issue_id === group.issue.id && event.is_current,
                    )
                      ? "Change recorded decision"
                      : "Record a review decision"}
                  </summary>
                  <div className="review-action-row">
                    <label>
                      Review decision
                      <select
                        value={decision}
                        onChange={(event) => setDecision(event.target.value)}
                      >
                        <option
                          value="accept_verified_extraction"
                          disabled={!group.evidence}
                        >
                          Accept verified extraction
                        </option>
                        <option
                          value="correct_transcription_or_normalization"
                          disabled={!group.evidence}
                        >
                          Correct transcription / normalization
                        </option>
                        <option value="mark_unresolved">Mark unresolved</option>
                        <option value="reject_unsupported_extraction">
                          Reject unsupported extraction
                        </option>
                        <option value="record_documented_assumption">
                          Record an internal-assumption decision
                        </option>
                      </select>
                    </label>
                    <label>
                      Rationale
                      <input
                        value={rationale}
                        onChange={(event) => setRationale(event.target.value)}
                        placeholder="Record why this decision is appropriate"
                      />
                    </label>
                    {correctionDecision ? (
                      <>
                        <label>
                          Corrected value
                          <input
                            value={correctedValue}
                            onChange={(event) =>
                              setCorrectedValue(event.target.value)
                            }
                            placeholder="Value from cited tender text"
                          />
                        </label>
                        <label>
                          Corrected unit
                          <input
                            value={correctedUnit}
                            onChange={(event) =>
                              setCorrectedUnit(event.target.value)
                            }
                            placeholder="For example, km"
                          />
                        </label>
                      </>
                    ) : null}
                    <button
                      disabled={
                        saving ||
                        !rationale.trim() ||
                        (evidenceDecision && !group.evidence) ||
                        (correctionDecision &&
                          (!correctedValue.trim() || !correctedUnit.trim()))
                      }
                      onClick={() =>
                        void submitDecision(
                          group.issue,
                          group.requirement,
                          group.evidence,
                          group.assumptionCategory,
                        )
                      }
                    >
                      Record decision
                    </button>
                  </div>
                </details>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No review blockers loaded"
          detail="Validated issues and clarification actions will appear here when extraction finishes."
        />
      )}
      {review?.rejected_candidates.length ? (
        <>
          <h3>Rejected or unresolved extraction candidates</h3>
          <div className="issue-grid">
            {review.rejected_candidates.map((candidate, index) => (
              <article
                className="issue-card"
                key={`${candidate.ordinal}-${index}`}
              >
                <Badge value="needs_review" />
                <h4>{candidate.title}</h4>
                <p>{candidate.reason}</p>
                {candidate.source ? (
                  <button
                    className="link-button"
                    onClick={() => onCitation(candidate.source!)}
                  >
                    View source
                  </button>
                ) : (
                  <span className="muted">
                    No matching source evidence was retained for this rejected
                    candidate.
                  </span>
                )}
              </article>
            ))}
          </div>
        </>
      ) : null}
      {review?.events.length ? (
        <details className="technical-details">
          <summary>Review decision history ({review.events.length})</summary>
          <ul className="audit-list">
            {review.events.map((event) => (
              <li key={event.id}>
                <strong>{humanLabel(event.action)}</strong>
                <Badge value={event.is_current ? "current" : "superseded"} />
                <span>
                  {event.reviewer} ·{" "}
                  {new Date(event.created_at).toLocaleString()}
                </span>
                <small>{event.rationale}</small>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
function SourceSectionExplorer({
  documentId,
  onCitation,
  onError,
}: {
  documentId: string;
  onCitation: (value: Evidence | Record<string, unknown>) => void;
  onError: (message: string) => void;
}) {
  const [spans, setSpans] = useState<SourceSpan[]>();
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      setSpans((await api.sourceSpans(documentId)).spans);
    } catch (caught) {
      onError(message(caught));
    } finally {
      setLoading(false);
    }
  };
  const open = async (span: SourceSpan) => {
    if (!span.span_id) return;
    try {
      const source = await api.sourceSpan(documentId, span.span_id);
      onCitation({ ...source, quote: source.text });
    } catch (caught) {
      onError(message(caught));
    }
  };
  return (
    <details className="source-section-explorer">
      <summary onClick={() => !spans && void load()}>
        {loading
          ? "Loading source sections…"
          : "Inspect tender source sections"}
      </summary>
      {spans?.length ? (
        <ul className="source-section-list">
          {spans.map((span) => (
            <li key={span.span_id}>
              <span>
                {span.page_number
                  ? `Page ${span.page_number}`
                  : "Page not recorded"}
                {span.section_name ? ` · ${span.section_name}` : ""}
              </span>
              <button
                type="button"
                className="link-button"
                onClick={() => void open(span)}
              >
                View section
              </button>
            </li>
          ))}
        </ul>
      ) : spans ? (
        <span className="muted">
          No readable source sections were retained for this document.
        </span>
      ) : null}
    </details>
  );
}

function BuildabilityReadiness({
  review,
  candidates,
  analysis,
  configurations,
  onMatch,
}: {
  review?: ReviewWorkspace;
  candidates?: CandidateResponse;
  analysis?: Analysis;
  configurations: Configuration[];
  onMatch: () => void;
}) {
  if (!review) return null;
  const blocking = review.issues.filter(
    (issue) => issue.issue?.blocking !== false,
  );
  const accepted = review.counts.accepted;
  const overEvidenceThreshold = blocking.length > 3;
  const eligibleCandidates =
    candidates?.candidates.filter((candidate) => candidate.eligible) ?? [];
  const hasFinalCandidate = configurations.some((configuration) =>
    ["feasible", "conditionally_feasible"].includes(configuration.status),
  );

  if (accepted === 0 || overEvidenceThreshold) {
    return (
      <section
        className="card buildability-readiness"
        aria-labelledby="buildability-title"
      >
        <p className="eyebrow">Buildability check</p>
        <h2 id="buildability-title">We cannot recommend a build yet</h2>
        <p>
          {accepted === 0
            ? "No tender requirement has passed citation validation."
            : `${blocking.length} tender details need verification before the catalog can be evaluated safely.`}{" "}
          Updating the catalog will not fix missing or unsupported tender
          evidence.
        </p>
        <div className="readiness-actions">
          <a className="secondary-button" href="#review">
            Review tender evidence
          </a>
          <a className="secondary-button" href="#scenarios">
            Explore an internal estimate
          </a>
        </div>
        <small>
          An internal estimate can compare catalog options, but it cannot make
          the tender compliant or ready to bid.
        </small>
      </section>
    );
  }

  if (!candidates) {
    return (
      <section
        className="card buildability-readiness"
        aria-labelledby="buildability-title"
      >
        <p className="eyebrow">Buildability check</p>
        <h2 id="buildability-title">
          Tender facts are ready for catalog matching
        </h2>
        <p>
          Compare the verified requirements with the selected immutable catalog
          snapshot before drawing a build conclusion.
        </p>
        <button type="button" className="secondary-button" onClick={onMatch}>
          Match catalog
        </button>
      </section>
    );
  }

  if (eligibleCandidates.length === 0 || analysis?.status === "infeasible") {
    return (
      <section
        className="card buildability-readiness buildability-negative"
        aria-labelledby="buildability-title"
      >
        <p className="eyebrow">Buildability check</p>
        <h2 id="buildability-title">
          We cannot build this from the selected catalog yet
        </h2>
        <p>
          No available candidate currently satisfies the verified requirements
          for this catalog snapshot. An administrator can add verified component
          capability, price and availability data in Catalog Administration,
          then refresh matching and re-run analysis.
        </p>
        <a
          className="secondary-button"
          href="http://localhost:8000/docs#/catalog"
          target="_blank"
          rel="noreferrer"
        >
          Catalog Administration
        </a>
      </section>
    );
  }

  if (hasFinalCandidate) {
    return (
      <section
        className="card buildability-readiness buildability-positive"
        aria-labelledby="buildability-title"
      >
        <p className="eyebrow">Buildability check</p>
        <h2 id="buildability-title">
          A catalog-backed configuration is available
        </h2>
        <p>
          Review the deterministic BOM, pricing, engineering margins and tender
          evidence before any engineering, commercial or bid approval.
        </p>
      </section>
    );
  }

  return null;
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
      assumptionCategory?: string;
      blocking: boolean;
      issue: Issue;
      requirement?: Requirement;
    }
  >();
  for (const record of issues) {
    const issue = record.issue ?? {};
    const requirement =
      issue.requirement_index == null
        ? undefined
        : requirements[issue.requirement_index];
    const assumptionCategory =
      requirement?.requirement.category ?? issueCategory(issue.detail);
    const title = issue.code
      ? humanLabel(issue.code)
      : "Tender detail needs clarification";
    const key = `${issue.code ?? "review"}-${assumptionCategory ?? "general"}`;
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
      assumptionCategory,
      blocking: issue.blocking !== false,
      issue: record,
      requirement,
    };
    const label = requirement
      ? humanLabel(requirement.requirement.attribute)
      : assumptionCategory
        ? humanLabel(assumptionCategory)
        : categoryFromDetail(issue.detail);
    if (label && !current.affected.includes(label))
      current.affected.push(label);
    groups.set(key, current);
  }
  return [...groups.values()];
}
function issueCategory(detail?: string) {
  return detail
    ?.match(/range|endurance|payload|altitude|speed|quantity/i)?.[0]
    ?.toLowerCase();
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
    return "Inspect the cited clause first; correct transcription only with source evidence, or record it as unresolved.";
  if (code?.includes("unit") || code?.includes("normal"))
    return "Inspect the tender, record “not specified in tender,” or create a unit-qualified internal assumption for an estimate.";
  return requirement
    ? `Inspect the tender, record “not specified,” or set an internal ${humanLabel(requirement.requirement.attribute).toLowerCase()} assumption for an estimate. Request clarification only if the procurement process allows it.`
    : "Inspect source sections, record “not specified in tender,” or create an internal assumption for an estimate. Request clarification only where the process allows it.";
}

function InventoryWorkspace({
  session,
  items,
  records,
  extraction,
  requirements,
  onChanged,
  onError,
}: {
  session?: Session;
  items: CatalogItem[];
  records: InventoryRecord[];
  extraction?: ExtractionRun;
  requirements: Requirement[];
  onChanged: (record: InventoryRecord) => void;
  onError: (message: string) => void;
}) {
  const [unlisted, setUnlisted] = useState(false);
  const [itemId, setItemId] = useState("");
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("payload");
  const [manufacturer, setManufacturer] = useState("");
  const [onHand, setOnHand] = useState("0");
  const [expected, setExpected] = useState("0");
  const [expectedOn, setExpectedOn] = useState("");
  const [location, setLocation] = useState("");
  const [rationale, setRationale] = useState("");
  const [requirementId, setRequirementId] = useState("");
  const [missingCategory, setMissingCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const isAdmin = session?.roles.includes("admin") ?? false;
  const expectsLater = Number(expected) > 0;
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin || busy) return;
    if (
      (!unlisted && !itemId) ||
      !location.trim() ||
      !rationale.trim() ||
      (expectsLater && !expectedOn)
    ) {
      onError(
        expectsLater && !expectedOn
          ? "Select the expected arrival date before recording stock that is due later."
          : "Choose a catalog item and record the stock location and rationale.",
      );
      return;
    }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        catalog_item_id: unlisted ? undefined : itemId,
        sku: unlisted ? sku : undefined,
        name: unlisted ? name : undefined,
        category: unlisted ? category : undefined,
        manufacturer: unlisted ? manufacturer : undefined,
        on_hand_quantity: Number(onHand),
        expected_quantity: Number(expected),
        expected_on: Number(expected) > 0 ? expectedOn || undefined : undefined,
        location,
        rationale,
        extraction_run_id: extraction?.trace_id,
        requirement_ids: requirementId ? [requirementId] : [],
        requirement_categories: missingCategory ? [missingCategory] : [],
      };
      onChanged(await api.createInventoryRecord(body));
      setRationale("");
      setOnHand("0");
      setExpected("0");
      setExpectedOn("");
    } catch (caught) {
      onError(message(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card section-card inventory-card" id="inventory">
      <SectionHeading
        title="Reconcile available stock"
        description="An administrator can tally on-hand or expected stock against tender needs. Counts are versioned overlays; catalog specifications and tender evidence stay immutable."
      />
      <Alert tone="info">
        <strong>Administrative control.</strong>{" "}
        {session?.subject ?? "The signed-in administrator"} owns each record
        they create. Only that same admin identity can revise its count or
        availability. Demo Mode simulates this identity; production uses
        verified OIDC claims.
      </Alert>
      {!isAdmin ? (
        <BlockedStep reason="Inventory reconciliation requires the admin role." />
      ) : (
        <form className="inventory-form" onSubmit={save}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={unlisted}
              onChange={(event) => setUnlisted(event.target.checked)}
            />
            Item is not yet in the validated catalog
          </label>
          {!unlisted ? (
            <label>
              Catalog item
              <select
                value={itemId}
                onChange={(event) => setItemId(event.target.value)}
                required
              >
                <option value="">Choose an item in stock</option>
                {items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.sku} · catalog {item.inventory_qty}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label>
                Internal SKU
                <input
                  value={sku}
                  onChange={(event) => setSku(event.target.value)}
                  required
                />
              </label>
              <label>
                Item name
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />
              </label>
              <label>
                Category
                <input
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  required
                />
              </label>
              <label>
                Manufacturer
                <input
                  value={manufacturer}
                  onChange={(event) => setManufacturer(event.target.value)}
                  required
                />
              </label>
            </>
          )}
          <label>
            Counted on hand
            <input
              type="number"
              min="0"
              value={onHand}
              onChange={(event) => setOnHand(event.target.value)}
              required
            />
          </label>
          <label>
            Expected later
            <input
              type="number"
              min="0"
              value={expected}
              onChange={(event) => setExpected(event.target.value)}
              required
            />
          </label>
          <label>
            Expected date
            <input
              type="date"
              value={expectedOn}
              onChange={(event) => setExpectedOn(event.target.value)}
              disabled={!expectsLater}
              required={expectsLater}
            />
            {expectsLater && !expectedOn ? (
              <span className="field-error" role="alert">
                Select the expected arrival date before recording stock that is
                due later.
              </span>
            ) : null}
          </label>
          <label>
            Stock location
            <input
              value={location}
              onChange={(event) => setLocation(event.target.value)}
              placeholder="Stores, assembly floor, supplier hold"
              required
            />
          </label>
          <label>
            Link to accepted tender requirement
            <select
              value={requirementId}
              onChange={(event) => setRequirementId(event.target.value)}
            >
              <option value="">No accepted requirement link</option>
              {requirements.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {humanLabel(entry.requirement.attribute)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Missing/ambiguous category
            <input
              value={missingCategory}
              onChange={(event) => setMissingCategory(event.target.value)}
              placeholder="For example, endurance"
            />
          </label>
          <label className="scenario-rationale">
            Count rationale and provenance
            <textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="How and when this stock was counted or confirmed"
              required
            />
          </label>
          <button disabled={busy}>
            {busy ? "Recording stock…" : "Record inventory"}
          </button>
        </form>
      )}
      {records.length ? (
        <div className="inventory-grid">
          {records.map((record) => (
            <article className="inventory-item" key={record.id}>
              <div>
                <Badge
                  value={
                    record.solver_eligible ? "catalog_linked" : "needs_review"
                  }
                />
                <h3>{record.name}</h3>
                <small>
                  {record.sku} · {humanLabel(record.category)}
                </small>
              </div>
              <dl>
                <div>
                  <dt>On hand</dt>
                  <dd>{record.current.on_hand_quantity}</dd>
                </div>
                <div>
                  <dt>Expected</dt>
                  <dd>
                    {record.current.expected_quantity}
                    {record.current.expected_on
                      ? ` on ${record.current.expected_on}`
                      : ""}
                  </dd>
                </div>
                <div>
                  <dt>Location</dt>
                  <dd>{record.current.location}</dd>
                </div>
                <div>
                  <dt>Recorded by</dt>
                  <dd>{record.current.recorded_by_subject}</dd>
                </div>
              </dl>
              {!record.solver_eligible ? (
                <p className="muted">
                  Pending catalog and engineering validation; tracked here but
                  excluded from the solver.
                </p>
              ) : null}
              <InventoryRevision
                record={record}
                session={session}
                extraction={extraction}
                onChanged={onChanged}
                onError={onError}
              />
              <details className="technical-details">
                <summary>Inventory history ({record.history.length})</summary>
                <ul className="audit-list">
                  {record.history.map((version) => (
                    <li key={version.id}>
                      <strong>
                        Version {version.version_number}:{" "}
                        {version.on_hand_quantity} on hand
                      </strong>
                      <span>
                        {version.recorded_by_subject} ·{" "}
                        {new Date(version.created_at).toLocaleString()}
                      </span>
                      <small>{version.rationale}</small>
                    </li>
                  ))}
                </ul>
              </details>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No manual stock recorded"
          detail="Use a catalog item to tally usable stock, or log an unlisted item for later catalog validation."
        />
      )}
    </section>
  );
}

function InventoryRevision({
  record,
  session,
  extraction,
  onChanged,
  onError,
}: {
  record: InventoryRecord;
  session?: Session;
  extraction?: ExtractionRun;
  onChanged: (record: InventoryRecord) => void;
  onError: (message: string) => void;
}) {
  const [onHand, setOnHand] = useState(String(record.current.on_hand_quantity));
  const [expected, setExpected] = useState(
    String(record.current.expected_quantity),
  );
  const [expectedOn, setExpectedOn] = useState(
    record.current.expected_on ?? "",
  );
  const [location, setLocation] = useState(record.current.location);
  const [rationale, setRationale] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedVersion, setSavedVersion] = useState<number>();
  const allowed =
    session?.roles.includes("admin") &&
    session.subject === record.owner_subject;
  const expectsLater = Number(expected) > 0;
  const validExpectedDate = !expectsLater || Boolean(expectedOn);
  const revise = async () => {
    if (!allowed || !rationale.trim() || !validExpectedDate || saving) return;
    setSaving(true);
    try {
      const updated = await api.reviseInventoryRecord(record.id, {
        on_hand_quantity: Number(onHand),
        expected_quantity: Number(expected),
        expected_on: Number(expected) > 0 ? expectedOn || undefined : undefined,
        location,
        rationale,
        extraction_run_id: extraction?.trace_id,
        requirement_ids: record.current.requirement_ids,
        requirement_categories: record.current.requirement_categories,
      });
      onChanged(updated);
      setSavedVersion(updated.current.version_number);
      setRationale("");
    } catch (caught) {
      onError(message(caught));
    } finally {
      setSaving(false);
    }
  };
  return (
    <details className="review-decision">
      <summary>
        {allowed
          ? "Update count or availability"
          : `Owned by ${record.owner_subject}`}
      </summary>
      {allowed ? (
        <div className="review-action-row">
          <label>
            On hand
            <input
              type="number"
              min="0"
              value={onHand}
              onChange={(event) => setOnHand(event.target.value)}
            />
          </label>
          <label>
            Expected later
            <input
              type="number"
              min="0"
              value={expected}
              onChange={(event) => setExpected(event.target.value)}
            />
          </label>
          <label>
            Expected date
            <input
              type="date"
              value={expectedOn}
              onChange={(event) => setExpectedOn(event.target.value)}
              disabled={!expectsLater}
              required={expectsLater}
            />
          </label>
          {expectsLater && !expectedOn ? (
            <span className="field-error" role="alert">
              Select the expected arrival date before saving stock that is due
              later.
            </span>
          ) : null}
          <label>
            Location
            <input
              value={location}
              onChange={(event) => setLocation(event.target.value)}
            />
          </label>
          <label>
            Reason for revision
            <input
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={!rationale.trim() || !validExpectedDate || saving}
            onClick={() => void revise()}
          >
            {saving
              ? "Saving inventory version…"
              : "Save new inventory version"}
          </button>
          {savedVersion ? (
            <span className="save-confirmation" role="status">
              Inventory version {savedVersion} saved. The catalog specification
              was not changed.
            </span>
          ) : null}
        </div>
      ) : (
        <p className="muted">
          Only the administrator who created this record may add an override
          version.
        </p>
      )}
    </details>
  );
}

function Scenarios({
  extraction,
  catalog,
  inventory,
  catalogItems,
  assumptionUnits,
  scenarios,
  onCreated,
  onOpenAnalysis,
  onError,
  prefill,
}: {
  extraction: ExtractionRun;
  catalog: CurrentCatalogVersion;
  inventory: InventoryRecord[];
  catalogItems: CatalogItem[];
  assumptionUnits: Record<string, string[]>;
  scenarios: Scenario[];
  onCreated: (scenario: Scenario) => void;
  onOpenAnalysis: (analysis: Analysis) => void | Promise<void>;
  onError: (message: string) => void;
  prefill?: { category: string; nonce: number };
}) {
  const [intent, setIntent] = useState("baseline");
  const [category, setCategory] = useState("range");
  const [attribute, setAttribute] = useState("range");
  const [value, setValue] = useState("");
  const [unit, setUnit] = useState("km");
  const [rationale, setRationale] = useState("");
  const [componentSkus, setComponentSkus] = useState("");
  const [inventoryIds, setInventoryIds] = useState<string[]>([]);
  const [assumptionDrafts, setAssumptionDrafts] = useState<
    {
      category: string;
      attribute: string;
      operator: string;
      value: number;
      unit: string;
      rationale: string;
      provenance: string;
    }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [assumptionError, setAssumptionError] = useState<string>();
  useEffect(() => {
    if (!prefill) return;
    setCategory(prefill.category);
    setAttribute(prefill.category);
    setUnit(assumptionUnits[prefill.category]?.[0] ?? "");
    setAssumptionError(undefined);
  }, [prefill, assumptionUnits]);
  const activeCatalogItemIds = useMemo(
    () => new Set(catalogItems.map((item) => item.id)),
    [catalogItems],
  );
  const compatibleInventory = (record: InventoryRecord) =>
    Boolean(
      record.solver_eligible &&
        record.catalog_item_id &&
        activeCatalogItemIds.has(record.catalog_item_id),
    );
  useEffect(() => {
    setInventoryIds((current) =>
      current.filter((id) => {
        const record = inventory.find((item) => item.id === id);
        return record ? compatibleInventory(record) : false;
      }),
    );
  }, [catalog.id, catalogItems, inventory]);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const assumptions = assumptionDrafts;
    if (
      value.trim() &&
      (!Number.isFinite(Number(value)) || !rationale.trim())
    ) {
      onError(
        "Enter a numeric assumption, explicit unit and rationale before estimating a scenario.",
      );
      return;
    }
    setBusy(true);
    try {
      const scenario = await api.startScenario({
        extraction_run_id: extraction.trace_id,
        catalog_version_id: catalog.id,
        analysis_date: new Date().toISOString().slice(0, 10),
        name: "Tender estimate scenarios",
        intent,
        reviewer: "local-demo-reviewer",
        rationale:
          rationale || "Baseline scenario without new internal assumptions.",
        assumptions,
        component_skus: componentSkus
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        inventory_record_ids: inventoryIds,
      });
      onCreated(scenario);
      const pollScenario = async (attempt = 0): Promise<void> => {
        if (
          attempt > 10 ||
          ["completed", "failed"].includes(scenario.analysis.state)
        )
          return;
        window.setTimeout(
          async () => {
            try {
              const next = await api.scenario(scenario.id);
              onCreated(next);
              void onOpenAnalysis(next.analysis);
              if (!["completed", "failed"].includes(next.analysis.state))
                await pollScenario(attempt + 1);
            } catch {
              onError(
                "The scenario was saved but its deterministic analysis could not be refreshed.",
              );
            }
          },
          Math.min(1000 * 2 ** attempt, 8000),
        );
      };
      void onOpenAnalysis(scenario.analysis);
      await pollScenario();
    } catch (caught) {
      onError(message(caught));
    } finally {
      setBusy(false);
    }
  };
  const addAssumption = () => {
    const supportedUnits = assumptionUnits[category] ?? [];
    if (
      !value.trim() ||
      !Number.isFinite(Number(value)) ||
      !unit.trim() ||
      !rationale.trim()
    ) {
      onError(
        "Enter a numeric proposed value, unit and rationale before adding an internal assumption.",
      );
      return;
    }
    if (!supportedUnits.includes(unit)) {
      setAssumptionError(
        `${humanLabel(unit || "unknown")} is not a supported unit for ${humanLabel(category)}. Select ${supportedUnits.map(humanLabel).join(" or ")}.`,
      );
      return;
    }
    setAssumptionError(undefined);
    setAssumptionDrafts((current) => [
      ...current.filter((item) => item.attribute !== attribute),
      {
        category,
        attribute,
        operator: "minimum",
        value: Number(value),
        unit,
        rationale,
        provenance: "internal_assumption",
      },
    ]);
    setValue("");
  };
  return (
    <section className="card section-card scenario-card" id="scenarios">
      <SectionHeading
        title="Explore build scenarios"
        description="Create a versioned internal estimate when a tender omits a capability. Tender facts stay immutable; assumptions never prove compliance."
      />
      <Alert tone="warning">
        <strong>Government tender safety boundary.</strong> A feasible scenario
        is an engineering/catalog estimate, not tender compliance, approval, bid
        readiness or flight certification.
      </Alert>
      <div className="scenario-labels" aria-label="Scenario decision labels">
        <span>
          Tender requirements verified:{" "}
          <strong>
            {extraction.state === "completed" &&
            extraction.review_state === "not_required"
              ? "Yes"
              : "No — unresolved"}
          </strong>
        </span>
        <span>
          Engineering/catalog feasibility:{" "}
          <strong>Estimated by deterministic solver</strong>
        </span>
        <span>
          Bid/compliance review: <strong>Required</strong>
        </span>
      </div>
      <form className="scenario-form" onSubmit={create}>
        <label>
          Scenario type
          <select
            value={intent}
            onChange={(event) => setIntent(event.target.value)}
          >
            <option value="baseline">Baseline</option>
            <option value="cost_optimized">Cost-optimized</option>
            <option value="performance_oriented">Performance-oriented</option>
          </select>
        </label>
        <label>
          Assumption category
          <select
            value={category}
            onChange={(event) => {
              const nextCategory = event.target.value;
              setCategory(nextCategory);
              setAttribute(nextCategory);
              setUnit(assumptionUnits[nextCategory]?.[0] ?? "");
              setAssumptionError(undefined);
            }}
          >
            <option value="range">Range</option>
            <option value="endurance">Endurance</option>
            <option value="payload">Payload</option>
            <option value="quantity">Quantity</option>
          </select>
        </label>
        <label>
          Minimum proposed value
          <input
            value={value}
            inputMode="decimal"
            onChange={(event) => setValue(event.target.value)}
            placeholder="Optional for baseline"
          />
        </label>
        <button
          type="button"
          className="secondary-button"
          onClick={addAssumption}
        >
          Add assumption
        </button>
        <label>
          Unit
          <select
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
            aria-invalid={Boolean(assumptionError)}
          >
            {(assumptionUnits[category] ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        {assumptionError ? (
          <span className="field-error" role="alert">
            {assumptionError}
          </span>
        ) : null}
        <label>
          Catalog SKU constraint (optional)
          <input
            value={componentSkus}
            onChange={(event) => setComponentSkus(event.target.value)}
            placeholder="Example: SYN-COMM-LR"
          />
        </label>
        <label className="scenario-rationale">
          Internal rationale and provenance
          <textarea
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            placeholder="Why this is an internal estimate; use tender text only when separately cited."
          />
        </label>
        <fieldset className="scenario-rationale inventory-selector">
          <legend>Manual inventory to snapshot for this scenario</legend>
          {inventory.length ? (
            inventory.map((record) => (
              <label className="checkbox-label" key={record.id}>
                <input
                  type="checkbox"
                  checked={inventoryIds.includes(record.id)}
                  disabled={!compatibleInventory(record)}
                  onChange={(event) =>
                    setInventoryIds((current) =>
                      event.target.checked
                        ? [...current, record.id]
                        : current.filter((value) => value !== record.id),
                    )
                  }
                />
                {record.name}: {record.current.on_hand_quantity} on hand
                {!record.solver_eligible
                  ? " — pending catalog validation"
                  : compatibleInventory(record)
                    ? ""
                    : ` — unavailable for ${humanLabel(catalog.version)}; recorded under a different catalog snapshot`}
              </label>
            ))
          ) : (
            <span className="muted">
              No manually reconciled stock is available.
            </span>
          )}
        </fieldset>
        <button disabled={busy}>
          {busy ? "Creating estimate…" : "Create scenario estimate"}
        </button>
      </form>
      {assumptionDrafts.length ? (
        <div className="assumption-list" aria-label="Scenario assumptions">
          {assumptionDrafts.map((item) => (
            <span key={item.attribute}>
              {humanLabel(item.attribute)}: {item.value} {item.unit}
              <button
                type="button"
                className="quiet-button"
                aria-label={`Remove ${humanLabel(item.attribute)} assumption`}
                onClick={() =>
                  setAssumptionDrafts((current) =>
                    current.filter(
                      (entry) => entry.attribute !== item.attribute,
                    ),
                  )
                }
              >
                Remove
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <p className="muted">
        Catalog capabilities and prices are taken from the immutable{" "}
        {humanLabel(catalog.version)} snapshot. An optional SKU constraint
        limits only that catalog component category; missing prices and
        engineering limits remain review blockers.
      </p>
      {scenarios.length ? (
        <div className="scenario-grid">
          {scenarios.map((scenario) => (
            <article className="scenario-result" key={scenario.id}>
              <Badge
                value={scenario.analysis.status ?? scenario.analysis.state}
              />
              <h3>{humanLabel(scenario.intent)} scenario</h3>
              <p>
                {scenario.assumptions.length
                  ? `${scenario.assumptions.length} internal assumption${scenario.assumptions.length === 1 ? "" : "s"} recorded.`
                  : "No new assumptions recorded."}
              </p>
              {scenario.component_preferences.length ? (
                <small>
                  Catalog constraint:{" "}
                  {scenario.component_preferences.join(", ")}
                </small>
              ) : null}
              {scenario.inventory_overlays.length ? (
                <small>
                  Manual inventory snapshot:{" "}
                  {scenario.inventory_overlays
                    .map(
                      (item) =>
                        `${item.name} (${item.on_hand_quantity} on hand)`,
                    )
                    .join(", ")}
                </small>
              ) : null}
              <dl>
                <div>
                  <dt>Tender requirements verified</dt>
                  <dd>
                    {scenario.tender_requirements_verified
                      ? "Yes"
                      : "No — unresolved"}
                  </dd>
                </div>
                <div>
                  <dt>Engineering/catalog feasibility</dt>
                  <dd>
                    {humanLabel(scenario.engineering_catalog_feasibility)}
                  </dd>
                </div>
                <div>
                  <dt>Assumptions outstanding</dt>
                  <dd>{scenario.assumptions_outstanding ? "Yes" : "No"}</dd>
                </div>
                <div>
                  <dt>Bid/compliance review</dt>
                  <dd>Required</dd>
                </div>
              </dl>
              <button
                className="secondary-button"
                onClick={() => void onOpenAnalysis(scenario.analysis)}
              >
                View solver analysis
              </button>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No scenario estimate yet"
          detail="Use baseline, cost-optimized or performance-oriented scenarios to compare deterministic alternatives. Missing tender facts stay visibly unresolved."
        />
      )}
    </section>
  );
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
        <span>
          Catalog snapshot:{" "}
          {humanLabel(result.catalog_version ?? "not available")}
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
        number={6}
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
                  <td>{platformName(configuration)}</td>
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
  const coveragePercent = configuration.requirement_coverage_bps / 100;
  const evaluated = configuration.evaluations.filter(
    (evaluation) => evaluation.result !== "not_applicable",
  );
  const satisfied = evaluated.filter((evaluation) =>
    ["satisfied", "exceeded"].includes(evaluation.result),
  ).length;
  const unresolved = evaluated.filter(
    (evaluation) => evaluation.result === "unknown",
  ).length;
  const failed = evaluated.filter(
    (evaluation) => evaluation.result === "failed",
  ).length;
  const issueGroups = summarizeConfigurationIssues(configuration.issues);
  return (
    <section className="card section-card" id="bom">
      <SectionHeading
        title="BOM, cost and coverage"
        description="All figures are backend-provided and tied to the immutable catalog snapshot and solver policy."
      />
      <Alert>
        <Badge value={configuration.status} />{" "}
        <strong>
          {configurationBuildMessage(configuration, unresolved, failed)}
        </strong>
        <p>
          {satisfied} of {evaluated.length} evaluated requirements are verified
          against this configuration. {unresolved} remain unverified
          {failed ? ` and ${failed} fail a tested constraint` : ""}.
        </p>
        {issueGroups.length ? (
          <details className="technical-details issue-summary">
            <summary>
              Show {issueGroups.length} grouped solver blocker
              {issueGroups.length === 1 ? "" : "s"}
            </summary>
            <ul className="audit-list">
              {issueGroups.map((issue) => (
                <li key={issue.label}>
                  <strong>{issue.label}</strong>
                  <span>
                    {issue.count} occurrence{issue.count === 1 ? "" : "s"}
                  </span>
                  <small>{issue.detail}</small>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
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
      <h3>Requirement coverage ({coveragePercent}%)</h3>
      <p className="coverage-explainer">
        {coveragePercent === 0
          ? "No evaluated tender requirement is currently verified against a deterministic catalog capability. This usually means the tender clause needs evidence or an approved capability mapping; adding a part alone will not make it pass."
          : `${satisfied} verified requirement${satisfied === 1 ? "" : "s"} are covered by catalog-backed facts. Remaining unknown or failed clauses still require review.`}
      </p>
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
                  {evaluation.requirement.attribute !==
                  evaluation.requirement.category ? (
                    <small>{humanLabel(evaluation.requirement.category)}</small>
                  ) : null}
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
                    <span className="assumption-evidence">
                      Internal scenario assumption — not tender evidence
                    </span>
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
function summarizeConfigurationIssues(issues: Configuration["issues"]) {
  const groups = new Map<
    string,
    { label: string; detail: string; count: number }
  >();
  for (const issue of issues) {
    const label = humanLabel(issue.code ?? "review blocker");
    const current = groups.get(label);
    if (current) current.count += 1;
    else
      groups.set(label, {
        label,
        detail: safeReason(issue.detail),
        count: 1,
      });
  }
  return [...groups.values()].sort(
    (left, right) =>
      right.count - left.count || left.label.localeCompare(right.label),
  );
}
function configurationBuildMessage(
  configuration: Configuration,
  unresolved: number,
  failed: number,
) {
  if (configuration.status === "infeasible")
    return "This configuration does not meet at least one tested mandatory constraint.";
  if (configuration.status === "needs_review")
    return unresolved
      ? "This is a provisional estimate, not a tender-compliant build."
      : "This configuration still needs human review before any decision.";
  if (failed)
    return "This configuration has tested constraint failures and cannot be recommended.";
  return "This configuration passed the deterministic checks represented in the catalog snapshot.";
}
function platformName(configuration: Configuration) {
  const platform = configuration.selections.find((selection) =>
    ["platform", "airframe"].includes(selection.item.category),
  );
  return platform
    ? `${platform.item.name} · ${platform.item.sku}`
    : "Platform not recorded";
}
function Report({
  report,
  extraction,
  configurations,
  selected,
  requirements,
  issues,
  scenarios,
  onCitation,
}: {
  report: Record<string, unknown>;
  extraction?: ExtractionRun;
  configurations: Configuration[];
  selected?: Configuration;
  requirements: Requirement[];
  issues: Issue[];
  scenarios: Scenario[];
  onCitation: (value: Evidence | Record<string, unknown>) => void;
}) {
  const configuration =
    selected ??
    configurations.find((item) =>
      item.labels.includes("balanced_recommended"),
    ) ??
    configurations.find((item) => item.status === "feasible") ??
    configurations[0];
  const tenderVerified =
    extraction?.state === "completed" &&
    extraction.review_state === "not_required";
  const satisfied =
    configuration?.evaluations.filter((item) =>
      ["satisfied", "exceeded"].includes(item.result),
    ) ?? [];
  const unresolved =
    configuration?.evaluations.filter(
      (item) =>
        !["satisfied", "exceeded", "not_applicable"].includes(item.result),
    ) ?? [];
  const assumptions = scenarios.flatMap((scenario) => scenario.assumptions);
  const questions = (report.clarification_questions ?? []) as string[];
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
      <article className="report-section">
        <h3>Decision summary</h3>
        <dl className="cost">
          <div>
            <dt>Configuration</dt>
            <dd>
              {configuration
                ? platformName(configuration)
                : "No valid configuration"}
            </dd>
          </div>
          <div>
            <dt>Engineering feasibility</dt>
            <dd>
              {humanLabel(
                configuration?.status ??
                  String(report.solver_status ?? "pending"),
              )}
            </dd>
          </div>
          <div>
            <dt>Tender verification</dt>
            <dd>
              {tenderVerified
                ? "Verified from cited requirements"
                : "Unresolved — review required"}
            </dd>
          </div>
          <div>
            <dt>Material cost</dt>
            <dd>{formatInr(configuration?.cost.material_paise)}</dd>
          </div>
          <div>
            <dt>Contingency</dt>
            <dd>{formatInr(configuration?.cost.contingency_paise)}</dd>
          </div>
          <div>
            <dt>Final estimated cost</dt>
            <dd>{formatInr(configuration?.cost.total_paise)}</dd>
          </div>
          <div>
            <dt>Total weight</dt>
            <dd>
              {configuration
                ? `${configuration.total_weight_g} g`
                : "Not available"}
            </dd>
          </div>
          <div>
            <dt>Payload capability</dt>
            <dd>
              {configuration ? `${configuration.payload_g} g` : "Not available"}
            </dd>
          </div>
          <div>
            <dt>Lead time</dt>
            <dd>
              {configuration
                ? `${configuration.lead_time_days} days`
                : "Not available"}
            </dd>
          </div>
        </dl>
      </article>
      <article className="report-section">
        <h3>Requirement coverage</h3>
        <p>
          {satisfied.length} solver-evaluated requirement
          {satisfied.length === 1 ? "" : "s"} satisfied or exceeded.
          {unresolved.length || issues.length
            ? ` ${Math.max(unresolved.length, issues.length)} requirement or review item remains unresolved.`
            : " No unresolved solver requirement remains."}
        </p>
        {satisfied.length ? (
          <ul className="report-list">
            {satisfied.map((evaluation) => (
              <li key={evaluation.requirement_id}>
                <strong>{humanLabel(evaluation.requirement.attribute)}</strong>:{" "}
                {safeReason(evaluation.explanation)}
              </li>
            ))}
          </ul>
        ) : null}
      </article>
      {configuration ? (
        <article className="report-section">
          <h3>Selected components</h3>
          <ul className="report-list">
            {configuration.selections.map((selection) => (
              <li key={`${selection.item.sku}-${selection.quantity}`}>
                {selection.item.name} · {selection.item.sku} ×{" "}
                {selection.quantity}
              </li>
            ))}
          </ul>
        </article>
      ) : null}
      <article className="report-section">
        <h3>Important tender evidence</h3>
        <ul className="report-list">
          {requirements.map(({ id, requirement }) => (
            <li key={id}>
              <strong>{humanLabel(requirement.attribute)}</strong>:{" "}
              {formatRequirementValue(
                requirement.original_value,
                requirement.original_unit,
              )}{" "}
              {requirement.validated_evidence[0] ? (
                <button
                  className="link-button"
                  onClick={() => onCitation(requirement.validated_evidence[0])}
                >
                  View source
                </button>
              ) : (
                <span className="assumption-evidence">
                  Evidence unavailable
                </span>
              )}
            </li>
          ))}
        </ul>
      </article>
      {assumptions.length ? (
        <article className="report-section">
          <h3>Internal assumptions</h3>
          <ul className="report-list">
            {assumptions.map((item) => (
              <li key={item.id}>
                {humanLabel(item.attribute)}:{" "}
                {formatRequirementValue(
                  item.original_value,
                  item.original_unit,
                )}{" "}
                — not tender evidence
              </li>
            ))}
          </ul>
        </article>
      ) : null}
      <article className="report-section">
        <h3>Warnings and compliance caveats</h3>
        <p>
          This is decision support, not tender compliance, bid approval,
          engineering certification or flight certification.
        </p>
      </article>
      <h3>Clarification questions</h3>
      <ul>
        {questions.map((question) => (
          <li key={question}>{question}</li>
        ))}
      </ul>
    </div>
  );
}
function TechnicalAudit({
  nodes,
  citations,
  report,
  onCitation,
}: {
  nodes: Record<string, unknown>[];
  citations: Record<string, unknown>[];
  report?: Record<string, unknown>;
  onCitation: (value: Evidence | Record<string, unknown>) => void;
}) {
  return (
    <section className="card section-card">
      <SectionHeading
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
        {report ? (
          <div className="technical-report-metadata">
            <h3>Technical report metadata</h3>
            <ul>
              {(
                (report.sections ?? []) as {
                  title: string;
                  facts: unknown[];
                }[]
              ).map((section) => (
                <li key={section.title}>
                  {humanLabel(section.title)} · {section.facts.length} grounded
                  fact{section.facts.length === 1 ? "" : "s"}
                </li>
              ))}
            </ul>
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
  if (error instanceof ApiError) {
    if (error.detail.includes("different catalog snapshot"))
      return error.detail;
    if (error.detail.includes("unit is not compatible")) return error.detail;
    return error.status === 422
      ? error.detail || "Check the entered value and try again."
      : error.status === 503
        ? "The local worker is unavailable. Retry shortly."
        : "The requested item is not available in this workflow state.";
  }
  return "The request could not be completed. Retry when the local API and worker are available.";
}
function downloadBrief(
  workspace: Workspace,
  requirements: Requirement[],
  issues: Issue[],
  scenarios: Scenario[],
  configurations: Configuration[],
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
    [
      "Safety boundary",
      "Decision support only — not a tender-compliance finding, bid approval or flight certification.",
    ],
    ...requirements.map((row) => [
      `Tender fact: ${humanLabel(row.requirement.attribute)}`,
      formatRequirementValue(
        row.requirement.original_value,
        row.requirement.original_unit,
      ),
    ]),
    ...requirements.flatMap((row) =>
      row.requirement.validated_evidence.map((evidence) => [
        `Source: ${humanLabel(row.requirement.attribute)}`,
        `${evidence.page_number ? `Page ${evidence.page_number}` : "Page not recorded"}${evidence.section_name ? ` · ${evidence.section_name}` : ""}${evidence.quote ? ` · ${evidence.quote}` : ""}`,
      ]),
    ),
    ...issues.map((issue) => [
      "Review blocker",
      issue.issue?.detail ?? "Tender detail needs clarification",
    ]),
    ...scenarios.flatMap((scenario) => [
      [
        `Scenario: ${humanLabel(scenario.intent)}`,
        `Tender requirements verified: ${scenario.tender_requirements_verified ? "Yes" : "No — unresolved"}; engineering/catalog feasibility: ${humanLabel(scenario.engineering_catalog_feasibility)}; assumptions outstanding: ${scenario.assumptions_outstanding ? "Yes" : "No"}; bid/compliance review: required.`,
      ],
      ...scenario.assumptions.map((assumption) => [
        `Internal assumption: ${humanLabel(assumption.attribute)}`,
        `${formatRequirementValue(assumption.original_value, assumption.original_unit)} · ${assumption.rationale} · ${humanLabel(assumption.provenance)}`,
      ]),
      ...scenario.inventory_overlays.map((inventory) => [
        `Manual inventory snapshot: ${inventory.name}`,
        `${inventory.sku} · on hand ${inventory.on_hand_quantity} · expected ${inventory.expected_quantity}${inventory.expected_on ? ` on ${inventory.expected_on}` : ""} · recorded by ${inventory.recorded_by_subject} · ${inventory.solver_eligible ? "catalog-linked solver input" : "pending catalog validation; excluded from solver"}`,
      ]),
    ]),
    ...configurations.map((candidate) => [
      `Alternative build: ${platformName(candidate)}`,
      `${humanLabel(candidate.status)} · material ${formatInr(candidate.cost.material_paise)} · total ${formatInr(candidate.cost.total_paise)} · ${candidate.total_weight_g} g · ${candidate.lead_time_days} days · ${candidate.immediately_buildable ? "available" : "not immediately buildable"}.`,
    ]),
    ...configurationRows,
  ];
  const csv = rows
    .map((row) =>
      row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(","),
    )
    .join("\n");
  const link = document.createElement("a");
  const objectUrl = URL.createObjectURL(
    new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" }),
  );
  link.href = objectUrl;
  link.download = "tender-decision-brief.csv";
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
