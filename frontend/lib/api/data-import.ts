/**
 * Data Import API —— G17 staged 导入工作台 client。
 *
 * 与后端 /api/v1/data-import/batches 端点族一一对应。创建批次走 XHR
 * （复用 upload.ts 的进度/超时/abort 纪律），其余走共享 transport。
 * 刷新页面后从服务器恢复批次与解释配置（不依赖 React 内存）。
 */

import { API_BASE } from './config';
import { apiFetch } from './transport';
import { getAccessToken } from '../auth/tokenStore';
import { invalidateCache } from './get-fast-path';

const LABEL = 'DataImport API error';

/** transport 的 apiFetch 不带 params 选项 —— query 显式拼接（值仅含安全字符）。 */
function q(sessionId: string): string {
  return `?session_id=${encodeURIComponent(sessionId)}`;
}

export type ImportMemberKind = 'vector' | 'table';
export type MemberStatus =
  | 'discovered' | 'planned' | 'previewed' | 'committing'
  | 'completed' | 'failed' | 'cancelled';

export interface ImportSource {
  source_id: string;
  original_name: string;
  sha256: string;
  size: number;
  ext: string;
}

export interface ImportMember {
  member_key: string;
  source_id: string;
  container: 'file' | 'gpkg' | 'kml' | 'zipset' | 'sheet';
  kind: ImportMemberKind;
  display_name: string;
  container_ref: string | null;
  fields: string[];
  geometry_type: string | null;
  feature_count: number | null;
  crs: string | null;
  crs_source: string;
  size_bytes: number | null;
  unsupported_reason: string | null;
  hints: Record<string, unknown>;
  status: MemberStatus;
  selected: boolean;
}

export interface ImportBatch {
  batch_id: string;
  status: string;
  session_id?: string;
  created_at?: string;
  updated_at?: string;
  expires_at?: string;
  plan_version?: number;
  sources?: ImportSource[];
  members?: ImportMember[];
  interpretations?: Record<string, Record<string, unknown>>;
  plan_hashes?: Record<string, string>;
  receipts?: Record<string, ImportReceipt>;
  notes?: string[];
  license?: string;
  attribution?: string;
}

export interface ImportBatchSummary {
  batch_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  file_count: number;
  member_count: number;
  selected_count: number;
  completed_count: number;
  plan_version: number;
  terminal: boolean;
  notes: string[];
}

export interface ImportReceipt {
  member_key: string;
  plan_hash: string;
  status: 'completed' | 'failed' | 'cancelled';
  output_kind?: 'feature_collection' | 'table' | null;
  output_ref?: string | null;
  output_name?: string;
  input_rows?: number | null;
  output_rows?: number | null;
  rejected_rows?: number | null;
  geometry_type?: string | null;
  duplicate?: boolean;
  quality_status?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  diagnostics_ref?: string | null;
}

export interface MemberConfig {
  mode?: 'auto' | 'points' | 'wkt' | 'table';
  encoding?: string | null;
  delimiter?: string | null;
  header_row?: number;
  x_column?: string | null;
  y_column?: string | null;
  wkt_column?: string | null;
  source_crs_declared?: string | null;
  field_types?: Record<string, string>;
  keep_fields?: string[] | null;
}

export interface PreviewRow {
  member_key: string;
  effective_mode?: string;
  columns?: string[];
  column_kinds?: Record<string, { suggested: string; observed: string }>;
  sample_rows?: Record<string, unknown>[];
  accepted_rows?: number;
  rejected_rows?: number;
  reject_kinds?: Record<string, number>;
  rejects_inline?: { row: number; reason: string; column?: string; detail?: string; raw?: string }[];
  sampling_disclosure?: string;
  crs?: { crs_source: string; crs: string | null; confirmed: boolean };
  location_preview?: { bbox: Record<string, number> } | null;
  requires_crs_confirmation?: boolean;
  notes?: string[];
  encoding_used?: string | null;
  delimiter_used?: string | null;
  truncated?: boolean;
  error?: { code: string; message: string };
  plan_hash?: string;
}

/** 创建导入批次（多文件 + 进度）。sessionId 来自已验证会话。 */
export function createImportBatch(
  files: File[],
  sessionId: string,
  onProgress?: (percent: number) => void,
  opts?: { signal?: AbortSignal; timeoutMs?: number; ownerToken?: string | null; license?: string; attribution?: string }
): Promise<ImportBatch> {
  const formData = new FormData();
  for (const f of files) {
    formData.append('files', f);
  }
  formData.append('session_id', sessionId);
  if (opts?.license) formData.append('license', opts.license);
  if (opts?.attribution) formData.append('attribution', opts.attribution);

  const requestId =
    globalThis.crypto?.randomUUID?.() ?? `imp-${Date.now().toString(36)}`;
  const timeoutMs = opts?.timeoutMs ?? 5 * 60_000;
  const externalSignal = opts?.signal;

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/api/v1/data-import/batches`);
    xhr.setRequestHeader('X-Request-ID', requestId);
    const accessToken = getAccessToken();
    if (accessToken) {
      xhr.setRequestHeader('Authorization', `Bearer ${accessToken}`);
    }
    if (opts?.ownerToken) {
      xhr.setRequestHeader('X-Session-Token', opts.ownerToken);
    }
    xhr.timeout = timeoutMs;

    const onAbort = () => xhr.abort();
    const cleanup = () => externalSignal?.removeEventListener('abort', onAbort);
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException('The operation was aborted.', 'AbortError'));
    };
    if (externalSignal) {
      if (externalSignal.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'));
        return;
      }
      externalSignal.addEventListener('abort', onAbort, { once: true });
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
          invalidateCache('/api/v1/data-import/batches');
        } catch {
          reject(new Error('解析响应失败'));
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText);
          reject(new Error(err.detail || `创建导入批次失败: ${xhr.status}`));
        } catch {
          reject(new Error(`创建导入批次失败: ${xhr.status}`));
        }
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error('网络错误'));
    };
    xhr.ontimeout = () => { cleanup(); reject(new Error(`上传超时 (${timeoutMs}ms)`)); };
    xhr.send(formData);
  });
}

export async function listImportBatches(
  sessionId: string,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<ImportBatchSummary[]> {
  const out = await apiFetch<{ batches: ImportBatchSummary[] }>(
    `/api/v1/data-import/batches${q(sessionId)}`,
    { signal: opts?.signal, label: LABEL, ownerToken: opts?.ownerToken }
  );
  return out.batches ?? [];
}

export async function getImportBatch(
  batchId: string,
  sessionId: string,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<ImportBatch> {
  return apiFetch<ImportBatch>(
    `/api/v1/data-import/batches/${batchId}${q(sessionId)}`,
    { signal: opts?.signal, label: LABEL, ownerToken: opts?.ownerToken }
  );
}

export async function saveImportPlan(
  batchId: string,
  sessionId: string,
  body: { member_configs?: Record<string, MemberConfig>; selected?: string[] | null; expected_plan_version?: number | null },
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; plan_version: number; plan_hashes: Record<string, string>; status: string }> {
  return apiFetch(`/api/v1/data-import/batches/${batchId}/plan${q(sessionId)}`, {
    method: 'POST',
    body: body as unknown as Record<string, unknown>,
    signal: opts?.signal,
    label: LABEL,
    ownerToken: opts?.ownerToken,
  });
}

export async function previewImportMembers(
  batchId: string,
  sessionId: string,
  body: { member_keys?: string[] | null; row_limit?: number },
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; plan_version: number; previews: PreviewRow[] }> {
  return apiFetch(`/api/v1/data-import/batches/${batchId}/preview${q(sessionId)}`, {
    method: 'POST',
    body: body as unknown as Record<string, unknown>,
    signal: opts?.signal,
    label: LABEL,
    ownerToken: opts?.ownerToken,
  });
}

export async function commitImportBatch(
  batchId: string,
  sessionId: string,
  body: { member_keys?: string[] | null; expected_plan_version?: number | null } = {},
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; status: string; members: string[]; poll: string }> {
  return apiFetch(`/api/v1/data-import/batches/${batchId}/commit${q(sessionId)}`, {
    method: 'POST',
    body: body as unknown as Record<string, unknown>,
    signal: opts?.signal,
    label: LABEL,
    ownerToken: opts?.ownerToken,
  });
}

export async function cancelImportBatch(
  batchId: string,
  sessionId: string,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; status: string }> {
  return apiFetch(`/api/v1/data-import/batches/${batchId}/cancel${q(sessionId)}`, {
    method: 'POST',
    signal: opts?.signal,
    label: LABEL,
    ownerToken: opts?.ownerToken,
  });
}

export async function getImportReceipts(
  batchId: string,
  sessionId: string,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; status: string; plan_version: number; receipts: Record<string, ImportReceipt>; notes: string[] }> {
  return apiFetch(`/api/v1/data-import/batches/${batchId}/receipts${q(sessionId)}`, {
    signal: opts?.signal,
    label: LABEL,
    ownerToken: opts?.ownerToken,
  });
}

export async function getImportDiagnostics(
  batchId: string,
  memberKey: string,
  sessionId: string,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; member_key: string; lines: number; rows: string[] }> {
  return apiFetch(
    `/api/v1/data-import/batches/${batchId}/diagnostics/${encodeURIComponent(memberKey)}${q(sessionId)}`,
    { signal: opts?.signal, label: LABEL, ownerToken: opts?.ownerToken }
  );
}

export async function deleteImportBatch(
  batchId: string,
  sessionId: string,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<{ batch_id: string; deleted: boolean }> {
  const out = await apiFetch<{ batch_id: string; deleted: boolean }>(
    `/api/v1/data-import/batches/${batchId}${q(sessionId)}`,
    { method: 'DELETE', signal: opts?.signal, label: LABEL, ownerToken: opts?.ownerToken }
  );
  invalidateCache('/api/v1/data-import/batches');
  return out;
}
