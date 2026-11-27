/**
 * Sampling Accuracy API — 概率抽样调查与精度核查工作台客户端（GOAL-20261127-03 / WP5）。
 *
 * 链路：buildFrame（分类栅格 → 冻结框）→ previewDesign（无副作用分配预览）→
 * createSurvey（设计 digest 不可变快照 + 确定性选样）→ recordReference /
 * progress（盲标 CAS append-only）→ runEstimate（设计一致估计）→
 * listEstimates（历史可回看）。全部请求走共享 transport（apiFetch）；
 * owner token 经 X-Session-Token header；POST 永不重放。
 *
 * 后端真相：app/api/routes/survey_sampling.py + app/schemas/survey_sampling_schema.py。
 * 类型以后端 schema 为准（frame payload 的 strata 是 [id, info] 对数组，
 * 客户端归一化为对象行，UI 不感知序列化细节）。
 */

import { apiFetch, isApiError } from './transport';

const SA_BASE = '/api/v1';
const SA_LABEL = 'Sampling accuracy API error';

export interface SamplingAccuracyRequestOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

function headers(options: SamplingAccuracyRequestOptions): Record<string, string> {
  const h: Record<string, string> = {};
  if (options.ownerToken) h['X-Session-Token'] = options.ownerToken;
  return h;
}

function withSession(sessionId: string): string {
  return `?${new URLSearchParams({ session_id: sessionId })}`;
}

/** 调查域 typed 错误信封（HTTP detail payload：code/detail/correction_hint）。 */
export interface SurveyErrorPayload {
  code: string;
  detail: string;
  correction_hint?: string;
}

/** 从 ApiError.body 提取调查域错误码/详情/修复指引（估计 422/409 信封）。 */
export function surveyErrorOf(err: unknown): SurveyErrorPayload {
  if (isApiError(err)) {
    const body = err.body as { detail?: unknown } | null | undefined;
    const d = body?.detail;
    if (d && typeof d === 'object') {
      const rec = d as Record<string, unknown>;
      return {
        code: String(rec.code ?? 'SURVEY_FAILED'),
        detail: String(rec.detail ?? err.message),
        ...(typeof rec.correction_hint === 'string' && rec.correction_hint
          ? { correction_hint: rec.correction_hint }
          : {}),
      };
    }
    if (typeof d === 'string' && d) {
      return { code: 'SURVEY_FAILED', detail: d };
    }
  }
  return {
    code: 'SURVEY_FAILED',
    detail: err instanceof Error ? err.message : String(err),
  };
}

// ── 抽样框（frame） ────────────────────────────────────────────────

/** 后端帧 payload 的层条目：{stratum_id, N_units, area, class_value}。 */
export interface FrameStratumRow {
  stratum_id: string;
  N_units: number;
  area: number;
  class_value: number | null;
}

/** 冻结抽样框 payload（shape 参照 app/lib/survey_sampling/frame.py build_frame）。 */
export interface FramePayload {
  frame_digest: string;
  frame_version?: string;
  crs?: string;
  class_dictionary?: Record<string, string>;
  source_ref?: string;
  total_units: number;
  total_area: number;
  pixel_area_spread?: number;
  pixel_area_tolerance?: number;
  excluded?: { nodata?: number; outside_dictionary?: number };
  /** 后端序列化为 [stratum_id, info] 对数组（排序稳定 digest）；兼容对象形态。 */
  strata: Array<[string, { N_units: number; area: number; class_value?: number | null }]>
    | Record<string, { N_units: number; area: number; class_value?: number | null }>;
}

export function normalizeFrameStrata(payload: FramePayload): FrameStratumRow[] {
  const raw = payload.strata;
  if (Array.isArray(raw)) {
    return raw.map(([id, info]) => ({
      stratum_id: String(id),
      N_units: Number(info?.N_units ?? 0),
      area: Number(info?.area ?? 0),
      class_value: info?.class_value ?? null,
    }));
  }
  return Object.entries(raw ?? {}).map(([id, info]) => ({
    stratum_id: String(id),
    N_units: Number(info?.N_units ?? 0),
    area: Number(info?.area ?? 0),
    class_value: info?.class_value ?? null,
  }));
}

export interface FrameResponse {
  success: boolean;
  payload: FramePayload;
}

export interface FrameListResponse {
  success: boolean;
  frames: FramePayload[];
}

export async function buildFrame(
  sessionId: string,
  params: { sourceRef: string; band: number; classDictionary: Record<string, string> },
  options: SamplingAccuracyRequestOptions = {},
): Promise<FrameResponse> {
  return apiFetch(`${SA_BASE}/survey/frame${withSession(sessionId)}`, {
    headers: headers(options),
    body: {
      source_ref: params.sourceRef,
      band: params.band,
      class_dictionary: params.classDictionary,
    },
    signal: options.signal,
  });
}

export async function listFrames(
  sessionId: string,
  options: SamplingAccuracyRequestOptions = {},
): Promise<FrameListResponse> {
  return apiFetch(`${SA_BASE}/survey/frames${withSession(sessionId)}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

// ── 设计预览（无副作用） ───────────────────────────────────────────

export type DesignStrategy = 'equal' | 'proportional' | 'neyman';

export interface DesignPreview {
  allocation: Record<string, number>;
  n_total: number;
  weights_W: Record<string, number>;
  warnings: string[];
  rationale: Record<string, unknown>;
  frame_digest?: string;
  total_area?: number;
}

export interface DesignPreviewResponse {
  success: boolean;
  preview: DesignPreview;
}

export async function previewDesign(
  sessionId: string,
  params: {
    frameDigest: string;
    strategy: DesignStrategy;
    nTotal?: number | null;
    targetSeOa?: number | null;
  },
  options: SamplingAccuracyRequestOptions = {},
): Promise<DesignPreviewResponse> {
  return apiFetch(`${SA_BASE}/survey/design/preview${withSession(sessionId)}`, {
    headers: headers(options),
    body: {
      frame_digest: params.frameDigest,
      strategy: params.strategy,
      ...(params.nTotal != null ? { n_total: params.nTotal } : {}),
      ...(params.targetSeOa != null ? { target_se_oa: params.targetSeOa } : {}),
    },
    signal: options.signal,
  });
}

// ── 创建调查（冻结设计 + 选样） ────────────────────────────────────

export interface SurveyCreateResponse {
  success: boolean;
  survey_id: string;
  design_digest: string;
  frame_digest: string;
  allocation: Record<string, number>;
  backup_per_stratum: number;
  sample_count: number;
  main_count: number;
}

export interface SurveyCreateParams {
  frameDigest: string;
  objective: string;
  targetTime?: string;
  strategy: DesignStrategy;
  nTotal?: number | null;
  targetSeOa?: number | null;
  seed: number;
  backupPerStratum: number;
  blindLabelling: boolean;
  maxNonresponseRate: number;
  notes?: string;
}

export async function createSurvey(
  sessionId: string,
  params: SurveyCreateParams,
  options: SamplingAccuracyRequestOptions = {},
): Promise<SurveyCreateResponse> {
  return apiFetch(`${SA_BASE}/survey/create${withSession(sessionId)}`, {
    headers: headers(options),
    body: {
      frame_digest: params.frameDigest,
      objective: params.objective,
      target_time: params.targetTime ?? '',
      strategy: params.strategy,
      ...(params.nTotal != null ? { n_total: params.nTotal } : {}),
      ...(params.targetSeOa != null ? { target_se_oa: params.targetSeOa } : {}),
      seed: params.seed,
      backup_per_stratum: params.backupPerStratum,
      blind_labelling: params.blindLabelling,
      max_nonresponse_rate: params.maxNonresponseRate,
      notes: params.notes ?? '',
    },
    signal: options.signal,
  });
}

// ── 调查详情 / 进度 ────────────────────────────────────────────────

/** 选样单元（blind=true 时后端剥离 stratum_id/unit_index）。 */
export interface SurveySample {
  sample_id: string;
  stratum_id?: string;
  unit_index?: number;
  x: number;
  y: number;
  lon: number;
  lat: number;
  inclusion_prob: number;
  weight: number;
  draw_rank: number;
  is_backup: boolean;
}

export interface DesignStratum {
  stratum_id: string;
  N_units: number;
  area: number;
}

export interface SurveyDoc {
  survey_id: string;
  design_digest: string;
  allocation: Record<string, number>;
  strata: DesignStratum[];
  weights_W: Record<string, number>;
  samples: SurveySample[];
  sample_count: number;
  revision: number;
  estimates: EstimatePayload[];
  blind_labelling?: boolean;
  objective?: string;
  seed?: number;
  backup_per_stratum?: number;
}

export interface SurveyDetailResponse {
  success: boolean;
  survey: SurveyDoc;
}

export async function fetchSurvey(
  sessionId: string,
  surveyId: string,
  params: { blind?: boolean } = {},
  options: SamplingAccuracyRequestOptions = {},
): Promise<SurveyDetailResponse> {
  const qs = new URLSearchParams({ session_id: sessionId });
  if (params.blind != null) qs.set('blind', String(params.blind));
  return apiFetch(`${SA_BASE}/survey/${encodeURIComponent(surveyId)}?${qs}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

export interface StratumProgress {
  planned: number;
  main: number;
  backup: number;
  labelled: number;
  nonusable: number;
  unvisited: number;
  response_rate: number;
}

export interface SurveyProgress {
  survey_id: string;
  design_digest: string;
  per_stratum: Record<string, StratumProgress>;
  total_planned: number;
  total_labelled: number;
  overall_response_rate: number;
  can_estimate: boolean;
}

export interface ProgressResponse {
  success: boolean;
  progress: SurveyProgress;
}

export async function fetchProgress(
  sessionId: string,
  surveyId: string,
  options: SamplingAccuracyRequestOptions = {},
): Promise<ProgressResponse> {
  return apiFetch(`${SA_BASE}/survey/${encodeURIComponent(surveyId)}/progress${withSession(sessionId)}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

// ── 调查记录（append-only + CAS） ──────────────────────────────────

/** 调查记录状态词表（封闭；contracts.RECORD_STATUSES）。 */
export type RecordStatus =
  | 'labelled'
  | 'undetermined'
  | 'refused'
  | 'pending'
  | 'not_visited'
  | 'disputed';

/** 参考证据类型（封闭词表）。 */
export type EvidenceType = 'field_visit' | 'high_res_imagery' | 'existing_survey' | 'other';

export type EvidenceQuality = 'high' | 'medium' | 'low';

export interface RecordParams {
  sampleId: string;
  status: RecordStatus;
  referenceClass?: string | null;
  evidenceType: EvidenceType;
  evidenceNote?: string;
  quality: EvidenceQuality;
  interpreter?: string;
  observedAt?: string;
  note?: string;
  expectedRevision?: number | null;
}

export interface RecordResult {
  recorded: boolean;
  sample_id: string;
  version: number;
  revision: number;
  blind: boolean;
}

export interface RecordResponse {
  success: boolean;
  result: RecordResult;
}

export async function postRecord(
  sessionId: string,
  surveyId: string,
  params: RecordParams,
  options: SamplingAccuracyRequestOptions = {},
): Promise<RecordResponse> {
  return apiFetch(`${SA_BASE}/survey/${encodeURIComponent(surveyId)}/records${withSession(sessionId)}`, {
    headers: headers(options),
    body: {
      sample_id: params.sampleId,
      status: params.status,
      ...(params.referenceClass ? { reference_class: params.referenceClass } : {}),
      evidence_type: params.evidenceType,
      evidence_note: params.evidenceNote ?? '',
      quality: params.quality,
      interpreter: params.interpreter ?? '',
      observed_at: params.observedAt ?? '',
      note: params.note ?? '',
      ...(params.expectedRevision != null ? { expected_revision: params.expectedRevision } : {}),
    },
    signal: options.signal,
  });
}

export async function postAdjudicate(
  sessionId: string,
  surveyId: string,
  params: { sampleId: string; referenceClass: string; note?: string; expectedRevision?: number | null },
  options: SamplingAccuracyRequestOptions = {},
): Promise<RecordResponse> {
  return apiFetch(`${SA_BASE}/survey/${encodeURIComponent(surveyId)}/adjudicate${withSession(sessionId)}`, {
    headers: headers(options),
    body: {
      sample_id: params.sampleId,
      reference_class: params.referenceClass,
      note: params.note ?? '',
      ...(params.expectedRevision != null ? { expected_revision: params.expectedRevision } : {}),
    },
    signal: options.signal,
  });
}

// ── 估计（设计一致） ───────────────────────────────────────────────

export interface MetricValue {
  estimate: number | null;
  se: number | null;
  ci_low: number | null;
  ci_high: number | null;
  evaluable: boolean;
  reason?: string;
  /** CI 越界 [0,1] 时的裁剪副本 {low, high}。 */
  ci_clipped?: { low: number | null; high: number | null } | null;
}

/** 估计 payload（shape 参照 estimation.StratifiedResult.as_dict）。 */
export interface EstimatePayload {
  status: 'supported' | 'partial' | 'not_evaluated' | 'stale' | string;
  limitations: string[];
  strata: string[];
  ref_classes: string[];
  /** (H,R) 估计比例；不可估 cell 为 null。 */
  matrix: Array<Array<number | null>>;
  sample_counts: number[][];
  n_responded: number[];
  n_planned: number[];
  overall_accuracy: MetricValue;
  user_accuracy: Record<string, MetricValue>;
  producer_accuracy: Record<string, MetricValue>;
  area_fraction: Record<string, MetricValue>;
  area_estimate: Record<string, MetricValue>;
  excluded_per_stratum?: number[] | null;
  excluded_total: number;
  excluded_reasons: Record<string, number>;
  estimator_version?: string;
  estimate_id?: string;
  survey_id?: string;
  design_digest?: string;
  created_at?: number;
}

export interface EstimateResponse {
  success: boolean;
  estimate: EstimatePayload;
}

export interface EstimatesListResponse {
  success: boolean;
  estimates: EstimatePayload[];
}

export async function runEstimate(
  sessionId: string,
  surveyId: string,
  options: SamplingAccuracyRequestOptions = {},
): Promise<EstimateResponse> {
  return apiFetch(`${SA_BASE}/survey/${encodeURIComponent(surveyId)}/estimate${withSession(sessionId)}`, {
    method: 'POST',
    headers: headers(options),
    signal: options.signal,
  });
}

export async function listEstimates(
  sessionId: string,
  surveyId: string,
  options: SamplingAccuracyRequestOptions = {},
): Promise<EstimatesListResponse> {
  return apiFetch(`${SA_BASE}/survey/${encodeURIComponent(surveyId)}/estimates${withSession(sessionId)}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

// ── 调查清单 ───────────────────────────────────────────────────────

export interface SurveyListResponse {
  success: boolean;
  surveys: Array<SurveyDoc & { survey_id: string }>;
}

export async function listSurveys(
  sessionId: string,
  options: SamplingAccuracyRequestOptions = {},
): Promise<SurveyListResponse> {
  return apiFetch(`${SA_BASE}/surveys${withSession(sessionId)}`, {
    headers: headers(options),
    signal: options.signal,
  });
}

export { SA_LABEL };
