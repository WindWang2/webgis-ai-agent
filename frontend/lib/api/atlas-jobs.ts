/**
 * 批量图册任务 API 客户端（atlas_job.v1）。
 *
 * 对应后端 `POST/GET /api/v1/atlas/jobs*`。提交是**异步 durable job**：
 * 与同步 vector-pdf 不同，不等待渲染 —— 轮询走统一任务中心
 *（lib/api/jobs，/tasks/jobs），本客户端只提供提交与图册详情（页级状态 +
 * 产物下载 URL）。普通交互与 Agent（export_atlas 工具）消费同一任务 API。
 */
import { apiFetch } from './transport';

/** atlas 分页策略（与同步 vector-pdf 的 AtlasRequestPolicy 同形，camelCase）。 */
export interface AtlasPolicyRequest {
  driver: 'frames' | 'category' | 'feature';
  layerId?: string;
  categoryProperty?: string;
  featuresPerPage?: number;
  pageBudget?: number;
  includeCover?: boolean;
  atlasTitle?: string;
}

export interface AtlasJobSubmitRequest {
  session_id: string;
  atlas: AtlasPolicyRequest;
  classification_scope?: 'shared' | 'local';
  target_dpi?: number;
  title?: string;
  simulated_data?: boolean;
}

/** 提交期 dry-run 预检摘要（纯规划，无渲染）。 */
export interface AtlasPlanSummary {
  page_count: number;
  cover: boolean;
  first_page_id: string;
  last_page_id: string;
  degradations: Array<{ code: string; detail: string }>;
}

export interface AtlasJobSubmitResponse {
  success: boolean;
  job_id: string;
  status: string;
  idempotent_reuse: boolean;
  message: string;
  plan: AtlasPlanSummary | null;
  poll_url: string;
}

/** 页级状态（JobView.page_status 与详情页同形）。 */
export interface AtlasPageStatus {
  page_id: string;
  title: string;
  page_number: number;
  status: 'rendered' | 'failed' | string;
  error: string | null;
}

export interface AtlasJobArtifacts {
  pdf: string | null;
  manifest: string | null;
  pdf_url: string | null;
  manifest_url: string | null;
  lineage_recorded?: boolean;
}

export interface AtlasJobDetailResponse {
  job: Record<string, unknown>;
  pages: AtlasPageStatus[];
  artifacts: AtlasJobArtifacts;
}

export async function submitAtlasJob(
  req: AtlasJobSubmitRequest,
  options: { ownerToken?: string | null; signal?: AbortSignal } = {},
): Promise<AtlasJobSubmitResponse> {
  return apiFetch<AtlasJobSubmitResponse>('/api/v1/atlas/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
    ownerToken: options.ownerToken ?? undefined,
    signal: options.signal,
    label: 'Atlas job submit API error',
  });
}

export async function getAtlasJob(
  jobId: string,
  options: { ownerToken?: string | null; signal?: AbortSignal } = {},
): Promise<AtlasJobDetailResponse> {
  return apiFetch<AtlasJobDetailResponse>(
    `/api/v1/atlas/jobs/${encodeURIComponent(jobId)}`,
    {
      method: 'GET',
      ownerToken: options.ownerToken ?? undefined,
      signal: options.signal,
      label: 'Atlas job detail API error',
    },
  );
}
