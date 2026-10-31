/**
 * Data Preparation API — 数据准备域客户端（inspect → propose → prepare → bind）.
 *
 * All requests flow through the shared transport (apiFetch) for typed errors;
 * owner token rides the SEC-08 X-Session-Token header (anonymous sessions).
 * Payloads are bounded by design on the server side (≤50 sample rows, no row
 * data in proposals/receipts) — the client mirrors those shapes as types only.
 */

import { apiFetch } from './transport';

const DP_LABEL = 'Data preparation API error';

export interface RoleCandidate {
  role: string;
  confidence: number;
  reasons: string[];
}

export interface FieldProposal {
  field: string;
  dtype: string;
  candidates: RoleCandidate[];
  rejected: [string, string][];
  needs_clarification: boolean;
}

export interface InspectionFinding {
  code: string;
  severity: 'ok' | 'warn' | 'blocked';
  message_zh: string;
  evidence: Record<string, unknown>;
  suggestion: string;
  field: string;
}

export interface SpatialReceipt {
  input_ref: string;
  geometry_type: string;
  srs: string;
  crs_source: string;
  axis_order_guessed: string;
  coordinate_range: number[] | null;
  encoding: string;
  time_fields: string[];
  tz_status: string;
  pk_field: string;
  pk_status: string;
  row_count: number;
  sample_rows_scanned: number;
  findings: InspectionFinding[];
  blocked: boolean;
}

export interface SampleFeature {
  properties: Record<string, unknown>;
  geometry_type: string;
  first_position: number[] | null;
}

export interface InspectResponse {
  input_ref: string;
  input_fingerprint: string;
  receipt: SpatialReceipt;
  proposals: FieldProposal[];
  sample: SampleFeature[];
  sample_truncated: boolean;
}

export interface PlanStep {
  type: string;
  [key: string]: unknown;
}

export interface PreparationPlan {
  plan_id: string;
  plan_hash: string;
  state: string;
  input_ref: string;
  input_fingerprint: string;
  steps: PlanStep[];
  mutates_data: boolean;
}

export interface PlanResponse {
  plan: PreparationPlan;
  warnings: string[];
}

export interface PreviewResponse {
  plan_id: string;
  sampled_rows: number;
  truncated: boolean;
  estimated_rows_after: number;
  quality_delta_sample: Record<string, number>;
  steps: Array<{ type: string; data_changed: boolean; detail: Record<string, unknown> }>;
  degraded: string[];
}

export interface PreparationReceipt {
  plan_id: string;
  plan_hash: string;
  input_ref: string;
  output_ref: string;
  output_fingerprint: string;
  row_before: number;
  row_after: number;
  quality_before: Record<string, number>;
  quality_after: Record<string, number>;
  degraded: string[];
  user_confirmations: Array<{ field: string; role: string; source: string }>;
}

export interface FieldBindingResponse {
  source_ref: string;
  descriptor_fingerprint: string;
  bound_fields: string[];
  skipped: [string, string][];
}

export interface RequestOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export const dataPreparationApi = {
  async inspect(
    req: { session_id: string; source_ref: string; user_hints?: Record<string, string> },
    opts?: RequestOptions,
  ): Promise<InspectResponse> {
    return apiFetch<InspectResponse>('/api/v1/data-preparation/inspect', {
      method: 'POST',
      body: req,
      ownerToken: opts?.ownerToken ?? undefined,
      signal: opts?.signal,
      label: DP_LABEL,
    });
  },

  async createPlan(
    req: { session_id: string; source_ref: string; steps: PlanStep[]; auto_steps?: boolean },
    opts?: RequestOptions,
  ): Promise<PlanResponse> {
    return apiFetch<PlanResponse>('/api/v1/data-preparation/plan', {
      method: 'POST',
      body: req,
      ownerToken: opts?.ownerToken ?? undefined,
      label: DP_LABEL,
    });
  },

  async previewPlan(
    planId: string,
    sessionId: string,
    opts?: RequestOptions,
  ): Promise<PreviewResponse> {
    return apiFetch<PreviewResponse>(
      `/api/v1/data-preparation/plan/${encodeURIComponent(planId)}/preview?session_id=${encodeURIComponent(sessionId)}`,
      {
        method: 'POST',
        ownerToken: opts?.ownerToken ?? undefined,
        label: DP_LABEL,
      },
    );
  },

  async executePlan(
    planId: string,
    sessionId: string,
    opts?: RequestOptions,
  ): Promise<{ receipt: PreparationReceipt }> {
    return apiFetch<{ receipt: PreparationReceipt }>(
      `/api/v1/data-preparation/plan/${encodeURIComponent(planId)}/execute?session_id=${encodeURIComponent(sessionId)}`,
      {
        method: 'POST',
        ownerToken: opts?.ownerToken ?? undefined,
        label: DP_LABEL,
      },
    );
  },

  async bindFields(
    req: { session_id: string; source_ref: string; roles: Record<string, string> },
    opts?: RequestOptions,
  ): Promise<FieldBindingResponse> {
    return apiFetch<FieldBindingResponse>('/api/v1/data-preparation/bindings/fields', {
      method: 'POST',
      body: req,
      ownerToken: opts?.ownerToken ?? undefined,
      label: DP_LABEL,
    });
  },
};
