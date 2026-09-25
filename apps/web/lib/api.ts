export type DocumentStatus = {
  document_id: string;
  document_version_id: string;
  state: string;
  source_span_count: number;
};
export type ExtractionRun = {
  trace_id: string;
  state: string;
  review_state: string;
  nodes: {
    node: string;
    attempt: number;
    status: string;
    elapsed_ms?: number;
  }[];
};
export type Requirement = {
  id: string;
  requirement: {
    category: string;
    attribute: string;
    semantics: string;
    operator: string;
    original_value: string | number | boolean | (string | number)[];
    original_unit: string;
    normalized_value?: string | number | boolean | (string | number)[];
    normalized_unit?: string;
    confidence: number;
    validated_evidence: Evidence[];
  };
};
export type Evidence = {
  document_id?: string;
  document_version_id?: string;
  span_id?: string;
  page_number?: number | null;
  section_name?: string | null;
  chunk_index?: number;
  quote?: string;
};
export type Cost = {
  material_paise: number | null;
  engineering_integration_paise: number;
  labour_paise: number;
  overhead_paise: number | null;
  contingency_paise: number | null;
  tax_paise: number | null;
  margin_paise: number | null;
  total_paise: number | null;
};
export type Configuration = {
  id: string;
  platform_id: string;
  status: string;
  outcome: string;
  labels: string[];
  total_weight_g: number;
  payload_g: number;
  lead_time_days: number;
  immediately_buildable: boolean;
  requirement_coverage_bps: number;
  cost: Cost;
  bom: {
    item_id: string;
    name: string;
    quantity: number;
    unit_cost_paise: number | null;
    subtotal_paise: number | null;
    inventory_qty: number;
  }[];
  evaluations: {
    requirement_id: string;
    result: string;
    explanation: string;
    requirement: {
      category: string;
      attribute: string;
      validated_evidence: Evidence[];
    };
  }[];
  issues: { detail: string }[];
};
export type CatalogCandidate = {
  item: {
    id: string;
    name: string;
    sku: string;
    category: string;
    availability: string;
    lifecycle_status?: string;
    lead_time_days: number;
    weight_grams?: number;
    cost_paise: number;
    specs?: Record<string, unknown>;
  };
  eligible: boolean;
  semantic_score: number;
  structured_match_score: number;
  final_score: number;
  breakdown: string[];
  penalties: {
    type: string;
    attribute: string;
    weight_kg: number;
    cost_paise: number;
  }[];
};
export type CandidateResponse = {
  candidates: CatalogCandidate[];
  missing_requirements: string[];
  missing_critical_categories?: string[];
  review_required?: boolean;
  extraction_review_state?: string;
};
export type CurrentCatalogVersion = {
  id: string;
  version: string;
  status: string;
  source: string;
  effective_from?: string | null;
  effective_to?: string | null;
};
export type Analysis = {
  id: string;
  state: string;
  status?: string;
  outcome?: string;
  summary?: { configuration_count?: number };
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}
const root = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${root}${path}`, {
    ...init,
    headers: { ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      body.detail?.detail ?? body.detail ?? "Request failed",
    );
  }
  return response.json() as Promise<T>;
}
export const api = {
  upload: (file: File) => {
    const data = new FormData();
    data.append("file", file);
    return request<DocumentStatus>("/v1/documents", {
      method: "POST",
      body: data,
    });
  },
  document: (id: string) => request<DocumentStatus>(`/v1/documents/${id}`),
  startExtraction: (version: string) =>
    request<ExtractionRun>(`/v1/document-versions/${version}/extractions`, {
      method: "POST",
    }),
  extraction: (id: string) =>
    request<ExtractionRun>(`/v1/extraction-runs/${id}`),
  requirements: (id: string) =>
    request<Requirement[]>(`/v1/extraction-runs/${id}/requirements`),
  extractionIssues: (id: string) =>
    request<Record<string, unknown>[]>(`/v1/extraction-runs/${id}/issues`),
  startAnalysis: (
    extraction_run_id: string,
    catalog_version_id: string,
    analysis_date: string,
  ) =>
    request<Analysis>("/v1/analyses", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        extraction_run_id,
        catalog_version_id,
        analysis_date,
      }),
    }),
  analysis: (id: string) => request<Analysis>(`/v1/analyses/${id}`),
  configurations: (id: string) =>
    request<{ configurations: Configuration[] }>(
      `/v1/analyses/${id}/configurations`,
    ),
  configuration: (analysis: string, configuration: string) =>
    request<Configuration>(
      `/v1/analyses/${analysis}/configurations/${configuration}`,
    ),
  retrieveCandidates: (run: string) =>
    request<CandidateResponse>(`/v1/catalog/retrieve-from-run/${run}`, {
      method: "POST",
    }),
  currentCatalog: () =>
    request<CurrentCatalogVersion>("/v1/catalog/versions/current"),
  startReport: (analysis_id: string) =>
    request<{ id: string }>("/v1/rag/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ analysis_id }),
    }),
  ragRun: (id: string) => request<{ state: string }>(`/v1/rag/runs/${id}`),
  report: (id: string) =>
    request<Record<string, unknown>>(`/v1/rag/runs/${id}/report`),
  ragNodes: (id: string) =>
    request<{ nodes: Record<string, unknown>[] }>(`/v1/rag/runs/${id}/nodes`),
  ragCitations: (id: string) =>
    request<{ citations: Record<string, unknown>[] }>(
      `/v1/rag/runs/${id}/citations`,
    ),
  resumeReport: (id: string) =>
    request<{ id: string }>(`/v1/rag/runs/${id}/resume`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        decision: "continue_provisional",
        reviewer: "local-demo-reviewer",
      }),
    }),
};
