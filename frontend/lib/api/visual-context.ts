/**
 * G36 视觉上下文证据 API client（ADR-0291）。
 *
 * 端点面：/api/v1/chat/sessions/{sid}/visual-context/*（受权 intake/列表/
 * 缩略图/撤销）。匿名会话经 X-Session-Token（transport ownerToken 通道）。
 * 回执只带 ref/元数据 —— bytes 只经 multipart intake 上行，不经聊天请求体。
 */
import { apiFetch, apiFetchBlob } from '@/lib/api/transport';

export type VisualEvidenceSourceType =
  | 'map_viewport'
  | 'map_region'
  | 'reference_upload';

export interface VisualEvidenceDto {
  session_id: string;
  evidence_id: string;
  source_type: VisualEvidenceSourceType | string;
  mime: string;
  origin_sha256: string;
  derived_sha256: string;
  width: number;
  height: number;
  origin_width?: number;
  origin_height?: number;
  mapspec_revision?: number | null;
  mapspec_fingerprint?: string;
  viewport_hash?: string;
  crop?: { x: number; y: number; w: number; h: number } | null;
  label?: string;
  state: 'draft' | 'bound' | 'revoked' | string;
  observations?: string[];
}

export interface VisualEvidenceIntakeOptions {
  sessionId: string;
  /** PNG/JPEG 字节（视口捕获 Blob 或用户上传 File）。 */
  file: Blob;
  sourceType: VisualEvidenceSourceType;
  label?: string;
  mapspecRevision?: number;
  mapspecFingerprint?: string;
  /** {center,zoom,bearing,pitch} —— 视口身份（地图类来源必填）。 */
  viewport?: Record<string, unknown>;
  dpr?: number;
  /** 原图设备像素矩形 {x,y,w,h}（map_region 必填）。 */
  crop?: { x: number; y: number; w: number; h: number };
  ownerToken?: string | null;
}

/** 受权 intake：multipart 上传 → 服务端真实解码 + 证据登记（draft）。 */
export async function uploadVisualEvidence(
  opts: VisualEvidenceIntakeOptions
): Promise<VisualEvidenceDto> {
  const form = new FormData();
  form.append('image', opts.file, opts.sourceType === 'reference_upload'
    ? (opts.file instanceof File ? opts.file.name : 'reference.png')
    : 'capture.png');
  form.append('source_type', opts.sourceType);
  if (opts.label) form.append('label', opts.label.slice(0, 120));
  if (opts.sourceType !== 'reference_upload') {
    if (typeof opts.mapspecRevision === 'number') {
      form.append('mapspec_revision', String(Math.max(0, Math.floor(opts.mapspecRevision))));
    } else {
      form.append('mapspec_revision', '0');
    }
    if (opts.mapspecFingerprint) {
      form.append('mapspec_fingerprint', opts.mapspecFingerprint.slice(0, 96));
    }
    if (opts.viewport) form.append('viewport', JSON.stringify(opts.viewport));
    form.append('dpr', String(opts.dpr ?? 1));
    if (opts.sourceType === 'map_region' && opts.crop) {
      form.append('crop', JSON.stringify(opts.crop));
    }
  }
  return apiFetch<VisualEvidenceDto>(
    `/api/v1/chat/sessions/${encodeURIComponent(opts.sessionId)}/visual-context/evidence`,
    {
      method: 'POST',
      body: form,
      ownerToken: opts.ownerToken,
      label: '视觉证据上传失败',
    }
  );
}

/** 会话证据列表（含 tombstone —— UI 显示「已移除」）。 */
export async function listVisualEvidence(
  sessionId: string,
  ownerToken?: string | null
): Promise<VisualEvidenceDto[]> {
  const resp = await apiFetch<{ evidence: VisualEvidenceDto[] }>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/visual-context/evidence`,
    { ownerToken, label: 'API Error' }
  );
  return resp.evidence ?? [];
}

/** 受权缩略图 URL（<img src>；授权经 owner token query/header 由 transport 注入）。 */
export async function fetchVisualEvidenceThumb(
  sessionId: string,
  evidenceId: string,
  ownerToken?: string | null
): Promise<Blob> {
  const r = await apiFetchBlob(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/visual-context/evidence/${encodeURIComponent(evidenceId)}/image?variant=thumbnail`,
    { ownerToken, label: 'API Error' }
  );
  return r.blob;
}

/** 撤销（draft/bound 均可；之后送模/读取均拒绝）。 */
export async function revokeVisualEvidence(
  sessionId: string,
  evidenceId: string,
  ownerToken?: string | null
): Promise<VisualEvidenceDto> {
  return apiFetch<VisualEvidenceDto>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/visual-context/evidence/${encodeURIComponent(evidenceId)}`,
    { method: 'DELETE', ownerToken, label: 'API Error' }
  );
}
