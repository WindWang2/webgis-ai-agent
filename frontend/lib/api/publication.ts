/**
 * C14：publication vector PDF 导出 API 客户端（POST /api/v1/export/vector-pdf）。
 *
 * 契约（app/schemas/map_schema.py VectorPdfRequest / VectorPdfExportResponse）：
 * - mapspec 必须由调用方内联 ref 载体源（服务端安全决策：不做 ref 水合；
 *   未内联 → 400 `mapspec_ref_sources_unhydrated` typed 拒绝）。
 * - atlas 可选分页策略（frames|category|feature；页数 ≤ 20 诚实封顶）。
 * - 503 `vector_pdf_unavailable` → 调用方回退栅格导出并披露；
 *   429 `vector_pdf_busy` → 稍后重试。
 */
import { apiFetch } from '@/lib/api/transport';

/** atlas 分页策略请求面（与后端 AtlasRequestPolicy 同形，camelCase）。 */
export interface AtlasRequestPolicy {
  driver: 'frames' | 'category' | 'feature';
  layerId?: string;
  categoryProperty?: string;
  featuresPerPage?: number;
  pageBudget?: number;
  includeCover?: boolean;
  atlasTitle?: string;
}

export interface VectorPdfExportResult {
  success: boolean;
  filename: string;
  url: string;
  format: 'pdf';
  vector: boolean;
  pages: number;
  frames_rendered: number;
  frames_skipped: number;
  target_dpi?: number;
  render_diagnostics?: Array<Record<string, unknown>>;
  schema_disclosures?: Array<Record<string, unknown>>;
  message?: string;
  lineage?: Record<string, unknown> | null;
  layout_version?: string;
  spec_fingerprint?: string;
  atlas?: boolean;
  atlas_pages?: Array<Record<string, unknown>> | null;
}

export interface VectorPdfExportParams {
  mapspec: Record<string, unknown>;
  title?: string;
  targetDpi?: number;
  sessionId?: string;
  atlas?: AtlasRequestPolicy;
}

export function exportVectorPdf(
  params: VectorPdfExportParams,
): Promise<VectorPdfExportResult> {
  return apiFetch<VectorPdfExportResult>('/api/v1/export/vector-pdf', {
    method: 'POST',
    body: {
      mapspec: params.mapspec,
      title: params.title,
      target_dpi: params.targetDpi,
      ...(params.sessionId ? { session_id: params.sessionId } : {}),
      ...(params.atlas ? { atlas: params.atlas } : {}),
    },
    timeoutMs: 150_000, // 路由 wait_for 120s + 余量（多页 atlas 最坏链路）
    label: 'Vector PDF export error',
  });
}

/** typed 错误码提取（FastAPI detail envelope {code, message}）。 */
export function vectorPdfErrorCode(err: unknown): string | null {
  const body = (err as { body?: { detail?: unknown } } | null | undefined)?.body;
  const detail = body?.detail;
  if (detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string') {
    return (detail as { code: string }).code;
  }
  if (typeof detail === 'string' && detail) return detail;
  const status = (err as { status?: number } | null | undefined)?.status;
  if (status === 503) return 'vector_pdf_unavailable';
  if (status === 429) return 'vector_pdf_busy';
  return null;
}
