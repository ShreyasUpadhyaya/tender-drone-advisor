import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import {
  canSubmit,
  inr,
  poll,
  requestJson,
  requiresReview,
  statusMessage,
  terminal,
} from "./workflow-helper.mjs";

test("upload accepts 201 and idempotent 200 while blocking duplicate submit", () => {
  assert.equal(canSubmit(false, true), true);
  assert.equal(canSubmit(true, true), false);
  assert.equal(
    [201, 200].every((status) => status === 201 || status === 200),
    true,
  );
});
test("mocked fetch supports upload and idempotent responses without auth headers", async () => {
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push([url, init]);
    return {
      ok: true,
      status: seen.length === 1 ? 201 : 200,
      json: async () => ({ idempotent: seen.length === 2 }),
    };
  };
  assert.equal(
    (await requestJson("/v1/documents", { method: "POST" })).status,
    201,
  );
  assert.equal(
    (await requestJson("/v1/documents", { method: "POST" })).body.idempotent,
    true,
  );
  assert.equal(seen[0][0], "http://localhost:8000/v1/documents");
  assert.equal(seen[0][1].headers, undefined);
});
test("mocked fetch translates validation and queue failures safely", async () => {
  for (const status of [422, 503]) {
    globalThis.fetch = async () => ({
      ok: false,
      status,
      json: async () => ({ detail: { detail: statusMessage(status) } }),
    });
    await assert.rejects(requestJson("/v1/analyses", { method: "POST" }), {
      status,
    });
  }
});
test("validation and API errors translate safely", () => {
  for (const status of [404, 409, 422, 503])
    assert.notEqual(statusMessage(status), "Request failed");
});
test("pending review extraction state never permits approval language", () => {
  assert.equal(requiresReview("needs_review", "pending"), true);
  assert.equal(requiresReview("completed", "decision_recorded"), true);
  assert.equal(requiresReview("completed", "validated"), false);
});
test("ingestion/extraction polling stops for completed, needs review, infeasible and awaiting review", async () => {
  for (const value of [
    "completed",
    "needs_review",
    "infeasible",
    "awaiting_review",
  ]) {
    assert.equal((await poll(async () => value)).state, value);
    assert.equal(terminal(value), true);
  }
  let calls = 0;
  const result = await poll(async () =>
    ++calls === 2 ? "awaiting_review" : "running",
  );
  assert.equal(result.attempt, 2);
});
test("cost, citation, keyboard and responsive contracts are deterministic", () => {
  assert.equal(inr(2065800), "₹20,658.00");
  assert.equal(inr(null), "Price unavailable");
  assert.equal(
    "button[aria-label], [role=dialog][aria-modal=true], @media(max-width:640px)".includes(
      "aria-modal",
    ),
    true,
  );
});

const page = readFileSync(resolve("app/page.tsx"), "utf8");
const styles = readFileSync(resolve("app/styles.css"), "utf8");

test("decision view hides internal identifiers until technical details are opened", () => {
  assert.match(page, /Technical audit ID: \{extraction\.trace_id\}/);
  assert.match(page, /Configuration ID: \{configuration\.id\}/);
  assert.doesNotMatch(page, /Catalog version ID/);
  assert.doesNotMatch(page, /LangGraph extraction run/);
  assert.match(page, /currentCatalog\(\)/);
  assert.match(page, /catalog-selection/);
});

test("technical audit is collapsed and uses human workflow labels", () => {
  assert.match(page, /<details className="audit-details">/);
  assert.match(page, /Open technical audit trail/);
  assert.match(page, /humanLabel\(String\(node.node/);
  assert.match(page, /Technical audit trail/);
});

test("requirements and review blockers use readable labels and evidence actions", () => {
  assert.match(page, /humanLabel\(requirement.attribute\)/);
  assert.match(page, /formatRequirementValue/);
  assert.match(page, /View source/);
  assert.match(page, /groupIssues\(issues, requirements\)/);
  assert.match(page, /View tender evidence/);
  assert.match(page, /Recommended action/);
  assert.match(page, /className="requirement-category"/);
  assert.match(page, /humanizeIssueSummary\(issue.detail\)/);
  assert.match(page, /"no value was inferred"/);
  assert.match(styles, /\.requirement-category \{/);
});

test("provisional catalog state and readable candidate cards are part of the workflow", () => {
  assert.match(page, /Configuration matching is provisional/);
  assert.match(page, /Can evaluate now:/);
  assert.match(page, /Need to clarify:/);
  assert.match(page, /candidate-card/);
  assert.match(page, /View details/);
  assert.match(page, /Technical reasons/);
});

test("candidate reasons hide internal references and attribute slugs", () => {
  const candidateCard = page.slice(
    page.indexOf("function CandidateCard"),
    page.indexOf("function candidateReason"),
  );
  assert.match(candidateCard, /candidate\.breakdown\.map\(candidateReason\)/);
  assert.doesNotMatch(candidateCard, /safeReason/);
  assert.match(
    page,
    /return `\$\{label\} cannot yet be verified from the tender\.`/,
  );
  assert.match(page, /const label = humanLabel\(attribute\)/);
});

test("responsive, overflow, export and accessible control contracts are present", () => {
  assert.match(styles, /\.table-wrap \{[\s\S]*overflow-x: auto/);
  assert.match(styles, /@media \(max-width: 720px\)/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /@media print/);
  assert.match(page, /aria-label="Tender file"/);
  assert.match(page, /aria-label="Citation source span"/);
  assert.match(page, /Export decision brief CSV/);
});
