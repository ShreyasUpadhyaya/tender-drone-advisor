export const formatInr = (paise: number | null | undefined) =>
  paise == null
    ? "Price unavailable"
    : new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
      }).format(paise / 100);
export const isTerminal = (state?: string | null) =>
  [
    "completed",
    "failed",
    "awaiting_review",
    "feasible",
    "conditionally_feasible",
    "infeasible",
    "needs_review",
  ].includes(state ?? "");
export const statusLabel = (state?: string | null) =>
  humanLabel(state ?? "not started");
export const requiresReview = (
  state?: string | null,
  reviewState?: string | null,
) =>
  state === "needs_review" ||
  ["pending", "needs_review", "decision_recorded", "awaiting_review"].includes(
    reviewState ?? "",
  );

const labelOverrides: Record<string, string> = {
  mtow: "Maximum take-off weight",
  gcs: "Ground control station",
  gcs_software: "Ground control software",
  ingress_protection: "Ingress protection",
  ground_control_points_visibility: "Ground-control-point visibility",
  needs_review: "Needs review",
  conditionally_feasible: "Conditionally feasible",
  in_stock: "In stock",
  load_document_spans: "Read tender",
  prepare_extraction_batches: "Prepare tender sections",
  extract_requirements: "Extract requirements",
  normalize_units: "Normalize units",
  validate_requirements: "Validate requirements",
  check_evidence: "Validate evidence",
  detect_conflicts: "Check conflicts",
  route_by_confidence: "Route uncertain details",
  retry_extraction: "Retry extraction",
  create_review_items: "Create review items",
  persist_results: "Save validated results",
  finalize_run: "Finish extraction",
  validate_request: "Validate request",
  load_extraction_context: "Load requirement context",
  retrieve_tender_evidence: "Find tender evidence",
  retrieve_catalog_context: "Find catalog context",
  load_or_run_solver: "Load solver result",
  validate_grounding: "Validate grounding",
  compose_report: "Compose report",
  apply_policy_guard: "Apply safety policy",
  persist_result: "Save report",
  finalize: "Finish report",
};

export function humanLabel(value?: string | null) {
  const raw = value ?? "not started";
  return (
    labelOverrides[raw] ??
    raw.replace(/[_-]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
  );
}

export function formatRequirementValue(
  value: string | number | boolean | (string | number)[] | undefined,
  unit?: string,
) {
  if (value === undefined || value === null || value === "unknown")
    return "Not specified";
  const rendered = Array.isArray(value)
    ? value.join(" to ")
    : value === true
      ? "Required"
      : value === false
        ? "Not required"
        : String(value);
  return unit && unit !== "unknown" ? `${rendered} ${unit}` : rendered;
}

export function safeReason(value: string) {
  return humanLabel(
    value
      .replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, "reference")
      .replace(/\b(?:requirement|span|item)[-_ ]?id\b/gi, "source reference"),
  );
}
