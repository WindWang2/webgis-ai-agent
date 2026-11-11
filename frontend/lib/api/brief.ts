/**
 * G11 证据化地图简报 API client（ADR-0288）。
 *
 * 端点面：/api/v1/briefs/*（草稿/编辑/证据/预检/冻结/下载/分享）。
 * 匿名会话经 X-Session-Token（transport ownerToken 通道）；写操作携带
 * expected_revision，服务端 409 = 乐观并发冲突（UI 引导刷新）。
 */
import { apiFetch, apiFetchBlob } from '@/lib/api/transport';

export interface BriefEvidenceBindingDto {
  artifact_ref: string;
  dataset_version_ref?: string;
  source_label: string;
  produced_at?: string;
  method?: string;
  stat_semantics?: string;
  unit?: string;
  field_path?: string;
  scope_spatial?: string;
  temporal_window?: string;
  artifact_sha256?: string;
}

export interface BriefFactDto {
  fact_id: string;
  label: string;
  kind: string;
  value: number;
  unit: string;
  status: 'verified' | 'warning' | 'unverified';
  status_reason?: string;
  origin: 'auto' | 'manual';
  basis: BriefEvidenceBindingDto;
  denominator_fact_id?: string;
  numerator_fact_id?: string;
  baseline_fact_id?: string;
}

export interface BriefMapViewDto {
  center: [number, number];
  zoom: number;
  pitch: number;
  bearing: number;
  visible_layer_ids: string[];
  mapspec_fingerprint: string;
  static_image_ref?: string;
}

export interface BriefBlockDto {
  id: string;
  chapter_id: string;
  kind:
    | 'text' | 'map_view' | 'metric' | 'chart' | 'table'
    | 'method' | 'limitation' | 'source';
  order: number;
  origin: 'auto' | 'manual';
  locked: boolean;
  text?: string;
  fact_id?: string;
  widget_id?: string;
  map_view?: BriefMapViewDto | null;
  binding?: BriefEvidenceBindingDto | null;
}

export interface BriefChapterDto {
  id: string;
  title: string;
  narrative: string;
  arc_role: string;
}

export interface MapBriefSpecDto {
  brief_schema_version: string;
  metadata: { title: string; session_id?: string; summary?: string };
  chapters: BriefChapterDto[];
  camera_keyframes: Array<{ chapter_id: string; center: [number, number]; zoom: number }>;
  linked_widgets: Array<{ id: string; kind: string; ref?: string; title?: string; chapter_id?: string }>;
  blocks: BriefBlockDto[];
  facts: BriefFactDto[];
  provenance: {
    session_id: string;
    mapspec_fingerprint: string;
    artifact_refs: string[];
    generator: string;
  };
}

export interface BriefSummaryDto {
  brief_id: string;
  revision: number;
  title: string;
  chapters: number;
  blocks: number;
  facts: number;
  fact_stats: { verified: number; warning: number; unverified: number };
}

export interface BriefResponseDto {
  brief_id: string;
  revision: number;
  spec: MapBriefSpecDto;
  stale: Array<{ block_id?: string; fact_id?: string; reason: string }>;
  summary: BriefSummaryDto;
}

export interface PreflightItemDto {
  check: string;
  verdict: 'pass' | 'fail' | 'not_evaluated';
  blocking: boolean;
  detail: string;
  refs: string[];
}

export interface PreflightReportDto {
  outcome: 'blocked' | 'draft_exportable' | 'deliverable';
  fact_stats: { verified: number; warning: number; unverified: number };
  items: PreflightItemDto[];
}

export interface BriefReleaseDto {
  release_id: string;
  release_no: number;
  frozen_revision: number;
  status: string;
  created_at: string | null;
  draft: boolean;
  fact_stats: Record<string, number>;
  counts: Record<string, number>;
  shared: boolean;
  share_expires_at: string | null;
}

export interface BriefBlockUpdateResultDto {
  saved: boolean;
  brief_id: string;
  revision: number;
  title: string;
  spec?: MapBriefSpecDto;
}

export interface EvidenceInspectionDto {
  fact: BriefFactDto;
  check: {
    status: string;
    reasons: string[];
    recompute: Record<string, number>;
    contradicted: boolean;
  };
  authoritative_value: number | null;
  dependencies: Record<
    string, { label: string; value: number; unit: string; status: string }
  >;
  artifact: { registered: boolean; record: Record<string, unknown> | null } | null;
}

interface BriefInit {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

function ownerHeaders(init?: BriefInit): { ownerToken?: string } {
  return init?.ownerToken ? { ownerToken: init.ownerToken } : {};
}

/** POST /briefs/sessions/{id}/draft —— 从会话真实产物生成草稿。 */
export function createBriefDraft(
  sessionId: string,
  payload: { title?: string } = {},
  init?: BriefInit,
): Promise<{ saved: boolean; brief_id: string; revision: number; summary: BriefSummaryDto }> {
  return apiFetch(`/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/draft`, {
    method: 'POST',
    body: payload,
    ...ownerHeaders(init),
    signal: init?.signal,
    label: '简报草稿生成失败',
  });
}

/** GET /briefs/sessions/{id} —— 完整 spec + stale 标记。 */
export function getBrief(sessionId: string, init?: BriefInit): Promise<BriefResponseDto> {
  return apiFetch(`/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}`, {
    ...ownerHeaders(init),
    signal: init?.signal,
    label: '简报读取失败',
  });
}

/** PATCH /briefs/sessions/{id}/blocks/{blockId} —— 块编辑（乐观 revision）。 */
export function patchBriefBlock(
  sessionId: string,
  blockId: string,
  patch: Partial<Pick<BriefBlockDto, 'text' | 'locked' | 'origin'>> & { order?: number },
  expectedRevision: number,
  init?: BriefInit,
): Promise<BriefBlockUpdateResultDto> {
  return apiFetch(
    `/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/blocks/${encodeURIComponent(blockId)}`,
    {
      method: 'PATCH',
      body: { expected_revision: expectedRevision, patch },
      ...ownerHeaders(init),
      signal: init?.signal,
      label: '简报块更新失败',
    },
  );
}

/** DELETE /briefs/sessions/{id}/blocks/{blockId} —— 删块。 */
export function deleteBriefBlock(
  sessionId: string,
  blockId: string,
  expectedRevision: number,
  init?: BriefInit,
): Promise<BriefBlockUpdateResultDto> {
  return apiFetch(
    `/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/blocks/${encodeURIComponent(blockId)}?expected_revision=${expectedRevision}`,
    {
      method: 'DELETE',
      ...ownerHeaders(init),
      signal: init?.signal,
      label: '简报块删除失败',
    },
  );
}

/** POST /briefs/sessions/{id}/preflight —— 发布预检。 */
export function runBriefPreflight(
  sessionId: string,
  init?: BriefInit,
): Promise<PreflightReportDto> {
  return apiFetch(`/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/preflight`, {
    method: 'POST',
    body: {},
    ...ownerHeaders(init),
    signal: init?.signal,
    label: '简报预检失败',
  });
}

/** POST /briefs/sessions/{id}/releases —— 冻结发布（幂等）。 */
export function freezeBriefRelease(
  sessionId: string,
  allowDraft: boolean,
  init?: BriefInit,
): Promise<{
  release_id: string;
  release_no: number;
  already_released: boolean;
  manifest: {
    draft: boolean;
    files: Array<{ name: string; status: string; bytes: number }>;
    evidence: Array<{ fact_id: string; status: string }>;
  };
}> {
  return apiFetch(`/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/releases`, {
    method: 'POST',
    body: { allow_draft: allowDraft },
    ...ownerHeaders(init),
    signal: init?.signal,
    label: '简报发布失败',
  });
}

/** GET /briefs/sessions/{id}/releases —— 版本列表。 */
export function listBriefReleases(
  sessionId: string,
  init?: BriefInit,
): Promise<{ releases: BriefReleaseDto[] }> {
  return apiFetch(`/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/releases`, {
    ...ownerHeaders(init),
    signal: init?.signal,
    label: '简报版本读取失败',
  });
}

/** GET /briefs/sessions/{id}/releases/{rid}/download —— 私有 HTML 下载。 */
export function downloadBriefReleaseHtml(
  sessionId: string,
  releaseId: string,
  init?: BriefInit,
): Promise<{ blob: Blob; filename: string | null }> {
  return apiFetchBlob(
    `/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/releases/${encodeURIComponent(releaseId)}/download`,
    { ...ownerHeaders(init), signal: init?.signal, label: '简报下载失败' },
  );
}

/** POST /briefs/sessions/{id}/releases/{rid}/share —— 显式分享。 */
export function shareBriefRelease(
  sessionId: string,
  releaseId: string,
  ttlDays = 7,
  init?: BriefInit,
): Promise<{ share_code: string; share_url: string; expires_at: string }> {
  return apiFetch(
    `/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/releases/${encodeURIComponent(releaseId)}/share`,
    {
      method: 'POST',
      body: { ttl_days: ttlDays },
      ...ownerHeaders(init),
      signal: init?.signal,
      label: '简报分享失败',
    },
  );
}

/** DELETE /briefs/sessions/{id}/releases/{rid}/share —— 撤销分享。 */
export function revokeBriefRelease(
  sessionId: string,
  releaseId: string,
  init?: BriefInit,
): Promise<{ revoked: boolean }> {
  return apiFetch(
    `/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/releases/${encodeURIComponent(releaseId)}/share`,
    {
      method: 'DELETE',
      ...ownerHeaders(init),
      signal: init?.signal,
      label: '简报分享撤销失败',
    },
  );
}

/** GET /briefs/sessions/{id}/evidence/{factId} —— 证据核对。 */
export function inspectBriefEvidence(
  sessionId: string,
  factId: string,
  init?: BriefInit,
): Promise<EvidenceInspectionDto> {
  return apiFetch(
    `/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/evidence/${encodeURIComponent(factId)}`,
    { ...ownerHeaders(init), signal: init?.signal, label: '证据核对失败' },
  );
}

/** POST /briefs/sessions/{id}/regenerate —— 重生成（保 manual/locked）。 */
export function regenerateBrief(
  sessionId: string,
  mode: 'metrics_only' | 'unlocked_text_only',
  expectedRevision: number,
  init?: BriefInit,
): Promise<BriefBlockUpdateResultDto> {
  return apiFetch(`/api/v1/briefs/sessions/${encodeURIComponent(sessionId)}/regenerate`, {
    method: 'POST',
    body: { expected_revision: expectedRevision, mode },
    ...ownerHeaders(init),
    signal: init?.signal,
    label: '简报重生成失败',
  });
}
