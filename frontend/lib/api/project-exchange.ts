/**
 * Project Exchange API 客户端（05 线，`/api/v1/projects/{pid}/exchange` 与
 * `/api/v1/project-exchange*`）。
 *
 * 契约源：`app/api/routes/project_exchange.py`。检视/计划（零写入）→ 导出
 * 提交（durable job，幂等）→ 按 job 查回执 → 认证下载；导入侧：包上传
 * （受控 ID）→ 预检 → 提交 → 按 job 查恢复报告。取消复用统一任务中心
 * `DELETE /api/v1/jobs/{id}`，不另建通道。
 */
import { apiFetch } from './transport';

const API = '/api/v1/project-exchange';

export type ExchangeScopeKind = 'project' | 'map' | 'analysis';

export interface ExchangeSelection {
  kind: ExchangeScopeKind;
  map_product_ids?: string[];
  workflow_ids?: string[];
}

export type Classification =
  | 'embedded'
  | 'external_reference'
  | 'redacted'
  | 'missing'
  | 'unsupported';

export interface ExchangeInspection {
  project_id: string;
  project_name: string;
  selection: Record<string, unknown>;
  reproduction_level: 'full_offline_recompute' | 'view_only' | 'partial';
  classification_counts: Record<string, number>;
  totals: { datasets: number; workflows: number; artifacts: number; maps: number };
  runnable_workflows: number;
  estimated_payload_bytes: number;
  estimated_unknown_bytes_count: number;
  disclosure: Array<{ kind: string; name: string; reason: string }>;
  truncated: Record<string, boolean>;
  exportable: boolean;
}

export interface ExchangePlan extends ExchangeInspection {
  datasets: Array<Record<string, unknown>>;
  workflows: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  maps: Array<Record<string, unknown>>;
  standard_files: {
    layers_gpkg: string | null;
    gpkg_layers: Array<{ logical_id: string; layer: string }>;
    rasters: Array<{ logical_id: string; path: string }>;
  };
  limits: { max_package_bytes: number; max_entries: number; max_entry_bytes: number };
}

export interface ExportSubmitResult {
  job_id: string;
  status: string;
  idempotent_reuse: boolean;
  message: string;
}

export interface ExportReceipt {
  package_id: string;
  filename: string;
  sha256: string;
  bytes: number;
  project_id: string;
  reproduction: Record<string, unknown>;
  counts: Record<string, number>;
  created_at: string;
  download_path: string;
}

export interface PackageUploadResult {
  package_upload_id: string;
  filename: string;
  bytes: number;
  sha256: string;
}

export interface ImportConflicts {
  declared_missing: string[];
  extra_entries: string[];
  duplicate_logical_ids: string[];
}

export interface ImportInspection {
  package_id: string;
  format: string;
  format_version: number;
  created_at: string;
  origin: { project_id?: string; project_name?: string; [k: string]: unknown };
  project_name: string;
  reproduction: { level: string; counts: Record<string, number> };
  classification_counts: Record<string, number>;
  totals: {
    datasets: number;
    workflows: number;
    artifacts: number;
    maps: number;
    files: number;
    zip_entries: number;
  };
  declared_total_bytes: number;
  licenses: Array<{
    logical_id: string;
    name: string;
    spdx: string | null;
    note: string;
    distributable: boolean | null;
    classification: string;
  }>;
  disclosure: Record<string, unknown>;
  conflicts: ImportConflicts;
  limits: { max_package_bytes: number; max_entries: number; max_entry_bytes: number };
}

export interface ImportSubmitResult {
  job_id: string;
  status: string;
  idempotent_reuse: boolean;
  message: string;
}

export interface ImportReceipt {
  import_id: string;
  job_id: string;
  package_id: string;
  project_id: string;
  target_name: string;
  mapping: Record<string, Record<string, unknown>>;
  session_writebacks: Array<Record<string, unknown>>;
  skipped: Array<Record<string, unknown>>;
  verification: Record<string, unknown>;
  created_at: string;
}

export interface JobView {
  id: string;
  kind: string;
  name: string;
  status: string;
  progress?: number | null;
  message?: string | null;
  error?: string | null;
}

export async function inspectProjectExchange(
  projectId: string,
  selection: ExchangeSelection = { kind: 'project' },
): Promise<ExchangeInspection> {
  return apiFetch<ExchangeInspection>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/exchange/inspect`,
    { method: 'POST', body: { selection } },
  );
}

export async function planProjectExchange(
  projectId: string,
  selection: ExchangeSelection = { kind: 'project' },
): Promise<ExchangePlan> {
  return apiFetch<ExchangePlan>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/exchange/plan`,
    { method: 'POST', body: { selection } },
  );
}

export async function submitProjectExport(
  projectId: string,
  selection: ExchangeSelection,
  packageName: string,
  sessionId?: string,
): Promise<ExportSubmitResult> {
  return apiFetch<ExportSubmitResult>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/exchange/export`,
    {
      method: 'POST',
      body: {
        selection,
        package_name: packageName,
        session_id: sessionId ?? null,
      },
    },
  );
}

export async function getExportReceiptByJob(jobId: string): Promise<ExportReceipt> {
  return apiFetch<ExportReceipt>(`${API}/jobs/${encodeURIComponent(jobId)}/export-receipt`);
}

export async function getExportReceipt(packageId: string): Promise<ExportReceipt> {
  return apiFetch<ExportReceipt>(
    `${API}/exports/${encodeURIComponent(packageId)}/receipt`,
  );
}

export async function uploadProjectPackage(file: File): Promise<PackageUploadResult> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<PackageUploadResult>(`${API}/packages/upload`, {
    method: 'POST',
    rawBody: form,
  });
}

export async function inspectImportPackage(
  packageUploadId: string,
): Promise<ImportInspection> {
  return apiFetch<ImportInspection>(`${API}/imports/inspect`, {
    method: 'POST',
    body: { package_upload_id: packageUploadId },
  });
}

export async function submitProjectImport(
  packageUploadId: string,
  targetName: string,
  acknowledgedConflicts: string[],
  sessionId?: string,
): Promise<ImportSubmitResult> {
  return apiFetch<ImportSubmitResult>(`${API}/imports`, {
    method: 'POST',
    body: {
      package_upload_id: packageUploadId,
      target_name: targetName,
      session_id: sessionId ?? null,
      acknowledged_conflicts: acknowledgedConflicts,
    },
  });
}

export async function getImportReceiptByJob(jobId: string): Promise<ImportReceipt> {
  return apiFetch<ImportReceipt>(`${API}/jobs/${encodeURIComponent(jobId)}/import-receipt`);
}

export async function getJob(jobId: string): Promise<JobView> {
  return apiFetch<JobView>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

/** 轮询直至终态（completed/failed/cancelled/stale）；intervalMs 默认 1500。 */
export async function pollJobTerminal(
  jobId: string,
  intervalMs = 1500,
  maxPolls = 200,
  getJobFn: (id: string) => Promise<JobView> = getJob,
): Promise<JobView> {
  for (let i = 0; i < maxPolls; i += 1) {
    const job = await getJobFn(jobId);
    if (['completed', 'failed', 'cancelled', 'stale'].includes(job.status)) {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('poll timeout');
}

export function blockingConflicts(conflicts: ImportConflicts): string[] {
  return [
    ...conflicts.declared_missing.map((p) => `declared_missing:${p}`),
    ...conflicts.duplicate_logical_ids.map((x) => `duplicate:${x}`),
  ];
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}
