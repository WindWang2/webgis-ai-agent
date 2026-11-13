/**
 * G35 时序地图动画媒体 API 客户端。
 *
 * 与 Pi 标准工具共用后端 ``app.services.temporal_media.service``；所有请求
 * 走 ``apiFetch``（统一超时 / X-Request-ID / ApiError / ownerToken →
 * X-Session-Token）。只传有界 refs 与有限选项，不传路径/媒体字节/shell 参数。
 */
import { apiFetch } from './transport';

export interface MediaObservation {
  id: string;
  label: string;
  start: string;
  end: string;
  feature_count?: number | null;
}

export interface MediaSegment {
  index: number;
  kind: 'observation' | 'held' | 'empty';
  observation_id: string | null;
  observation_label: string;
  observation_start: string;
  observation_end: string;
  time_label: string;
  identity: string;
  missing_period: boolean;
  held_from: string | null;
  duration_ms: number;
  encoded_repeat: number;
  encoded_durations_ms: number[];
}

export interface MediaPlanRequest {
  session_id: string;
  expected_revision?: number | null;
  from_frame_id?: string | null;
  to_frame_id?: string | null;
  frame_ids?: string[] | null;
  order?: 'chronological' | 'reverse';
  timing_policy?: 'equal' | 'proportional';
  dwell_ms?: number;
  fps?: number;
  missing_display?: 'skip' | 'empty' | 'hold';
  time_display?: 'label' | 'label_window' | 'none';
  width?: number;
  height?: number;
  format?: 'apng' | 'gif';
  loop?: boolean;
  partial_policy?: 'blocking' | 'allow_partial';
}

export interface MediaPlanPreview {
  plan_hash: string;
  /** 16 位短句柄：生产 dispatch 会截断长字符串，LLM/UI 一律用它提交。 */
  plan_ref: string;
  /** 全部真实观测桶（不受 frame_ids 子集筛选影响），供勾选面板使用。 */
  all_observations: MediaObservation[];
  plan: {
    observations: MediaObservation[];
    missing_periods: Array<{ id: string; label: string; start: string; end: string }>;
    selection: { omitted_observations: number; actual_span_ms: number; selection: string };
    display: {
      segments: MediaSegment[];
      timing: { policy: string; total_display_ms: number; rule: string;
        quantization_error_ms: number; compressed_observations?: string[] };
      order: string;
      missing_display: string;
      observation_count: number;
      missing_period_count: number;
      encoded_frame_count: number;
    };
    canvas: { width: number; height: number };
    format: string;
    partial: { is_partial: boolean; omitted_layers: Array<{ layer_id: string; reason: string }> };
    disclosures: string[];
    resource_estimate: { unique_digital_frames: number; encoded_frames: number;
      raw_pixel_bytes: number; estimated_temp_bytes: number; total_display_ms: number };
  };
}

export interface MediaCapabilities {
  formats: Array<{ name: string; available: boolean; core: boolean;
    color?: string; limitation?: string; reason?: string }>;
  renderer: { available: boolean; engine: string; rasterizer_version?: string; reason?: string };
  limits: { max_observations: number; max_encoded_frames: number;
    max_total_display_ms: number; width: number[]; height: number[] };
  binding: { available: boolean; code?: string; message?: string; observations?: number };
}

export interface MediaArtifact {
  filename: string;
  bytes: number;
  media_type: string;
  checksum: string;
  url: string;
}

export interface MediaJobStatus {
  success: boolean;
  status: 'pending' | 'running' | 'completed' | 'cancelled' | 'failed';
  phase: string;
  progress: number | null;
  message: string;
  completed: boolean;
  rendered_frames?: number;
  cache_hits?: number;
  encoded_frames?: number;
  artifacts?: Record<string, MediaArtifact> | null;
  verification?: { passed: boolean; decoded_frames: number; checks_count: number } | null;
  error?: unknown;
}

export interface MediaSubmitReceipt {
  success: boolean;
  status: 'pending' | 'completed';
  job_id: string;
  plan_hash: string;
  idempotent_reuse?: boolean;
  artifacts?: Record<string, MediaArtifact>;
}

export interface MediaJobResult {
  success: boolean;
  status: 'completed';
  job_id: string;
  artifacts: Record<string, MediaArtifact>;
  verification: { passed: boolean; decoded_frames: number; checks_count: number };
  manifest_summary: { observation_count: number; missing_periods: number;
    segments: number; total_display_ms: number; disclosures: string[] };
}

export interface MediaJobListItem {
  job_id: string;
  status: string;
  phase: string;
  progress: number | null;
  plan_hash: string;
  message: string;
  /** 服务端创建时间（epoch 秒）；恢复时取时间上最新而不是 job_id 字典序。 */
  created_at?: number;
}

function scope(ownerToken?: string | null) {
  return { ownerToken: ownerToken ?? null, label: 'Temporal media API' };
}

export async function getMediaCapabilities(
  sessionId: string, ownerToken?: string | null,
): Promise<{ success: boolean; capabilities: MediaCapabilities;
  binding: MediaCapabilities['binding'] }> {
  return apiFetch(
    `/api/v1/temporal-media/capabilities?session_id=${encodeURIComponent(sessionId)}`,
    { ...scope(ownerToken) },
  );
}

export async function previewMediaPlan(
  request: MediaPlanRequest, ownerToken?: string | null,
): Promise<MediaPlanPreview> {
  return apiFetch('/api/v1/temporal-media/plan', {
    ...scope(ownerToken), method: 'POST', body: request,
  });
}

export async function submitMediaJob(
  request: MediaPlanRequest & { plan_ref: string; plan_hash?: string },
  ownerToken?: string | null,
): Promise<MediaSubmitReceipt> {
  return apiFetch('/api/v1/temporal-media/submit', {
    ...scope(ownerToken), method: 'POST', body: request,
  });
}

export async function getMediaJobStatus(
  sessionId: string, jobId: string, ownerToken?: string | null,
): Promise<MediaJobStatus> {
  return apiFetch(
    `/api/v1/temporal-media/jobs/${encodeURIComponent(jobId)}?session_id=${encodeURIComponent(sessionId)}`,
    { ...scope(ownerToken) },
  );
}

export async function cancelMediaJob(
  sessionId: string, jobId: string, ownerToken?: string | null,
): Promise<MediaJobStatus> {
  return apiFetch(
    `/api/v1/temporal-media/jobs/${encodeURIComponent(jobId)}?session_id=${encodeURIComponent(sessionId)}`,
    { ...scope(ownerToken), method: 'DELETE' },
  );
}

export async function getMediaJobResult(
  sessionId: string, jobId: string, ownerToken?: string | null,
): Promise<MediaJobResult> {
  return apiFetch(
    `/api/v1/temporal-media/jobs/${encodeURIComponent(jobId)}/result?session_id=${encodeURIComponent(sessionId)}&refresh_urls=true`,
    { ...scope(ownerToken) },
  );
}

export async function listMediaJobs(
  sessionId: string, ownerToken?: string | null,
): Promise<{ success: boolean; jobs: MediaJobListItem[] }> {
  return apiFetch(
    `/api/v1/temporal-media/jobs?session_id=${encodeURIComponent(sessionId)}`,
    { ...scope(ownerToken) },
  );
}
