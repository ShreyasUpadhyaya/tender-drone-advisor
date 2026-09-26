export const terminal = (state) =>
  [
    "completed",
    "failed",
    "awaiting_review",
    "feasible",
    "conditionally_feasible",
    "infeasible",
    "needs_review",
  ].includes(state);
export const statusMessage = (status, detail = "Request failed") =>
  ({
    404: "Resource not found",
    409: "Invalid workflow state",
    422: "Validation error",
    503: "Queue or provider unavailable",
  })[status] ?? detail;
export async function poll(fetchState, max = 4) {
  for (let attempt = 0; attempt < max; attempt += 1) {
    const state = await fetchState();
    if (terminal(state)) return { state, attempt: attempt + 1 };
  }
  throw new Error("poll_timeout");
}
export const canSubmit = (busy, ready) => !busy && ready;
export const requiresReview = (state, reviewState) =>
  state === "needs_review" ||
  ["pending", "needs_review", "decision_recorded", "awaiting_review"].includes(
    reviewState,
  );
export const inr = (paise) =>
  paise == null
    ? "Price unavailable"
    : `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
export const humanLabel = (value) =>
  String(value)
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
export async function requestJson(path, init = {}) {
  const response = await globalThis.fetch(`http://localhost:8000${path}`, init);
  const body = await response.json();
  if (!response.ok)
    throw Object.assign(
      new Error(body.detail?.detail ?? body.detail ?? "Request failed"),
      { status: response.status },
    );
  return { status: response.status, body };
}
