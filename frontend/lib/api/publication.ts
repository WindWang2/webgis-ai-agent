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
  driver: 'frames' | 'category' | 'feature' | 'product';
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
  /** G04（#1999）：打印档求解 profile（a4_landscape 等；缺席 = not_evaluated）。 */
  pageProfile?: string;
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
      ...(params.pageProfile ? { page_profile: params.pageProfile } : {}),
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

// ── G26：统一混合地图导出（POST /api/v1/export/mixed）──────────────────────
//
// 契约（app/schemas/map_schema.py MixedMapExportRequest）：服务端从会话
// 权威 MapSpec 冻结修订编译（无客户端载荷 —— 栅格 ref owner 校验在服务端
// 解析层执行）；blocking 默认（会遗漏层 → 409 EXPORT_BLOCKED_INCOMPLETE）。

export interface MixedMapExportResult {
  success: boolean;
  filename: string;
  url: string;
  manifest_url?: string | null;
  format: 'png' | 'svg' | 'pdf';
  media_type: string;
  bytes: number;
  mapspec_revision?: number | null;
  partial: boolean;
  omitted_layers: Array<{ layer_id: string; reason: string; message?: string }>;
  raster_layers: Array<{
    layer_id: string; style_id?: string; data_revision?: string;
    overlap?: string; embedded?: boolean; width?: number; height?: number;
  }>;
  renderer: Record<string, unknown>;
  checksum?: string;
  render_diagnostics?: Array<Record<string, unknown>>;
  message?: string;
}

export interface MixedMapExportParams {
  sessionId: string;
  format: 'png' | 'svg' | 'pdf';
  expectedRevision?: number;
  partialPolicy?: 'blocking' | 'allow_partial';
  targetDpi?: number;
  title?: string;
}

export function exportMixedMap(
  params: MixedMapExportParams,
): Promise<MixedMapExportResult> {
  return apiFetch<MixedMapExportResult>('/api/v1/export/mixed', {
    method: 'POST',
    body: {
      session_id: params.sessionId,
      format: params.format,
      ...(params.expectedRevision != null
        ? { expected_revision: params.expectedRevision }
        : {}),
      partial_policy: params.partialPolicy ?? 'blocking',
      ...(params.targetDpi ? { target_dpi: params.targetDpi } : {}),
      ...(params.title ? { title: params.title } : {}),
    },
    timeoutMs: 150_000, // 与 vector-pdf 同预算（PDF worker 110s + 余量）
    label: 'Mixed map export error',
  });
}

/**
 * 混合导出 typed 错误码提取（与 vectorPdfErrorCode 同信封）。
 * 后端码表大小写不一（mixed 面 = RASTER_ERROR_CODES 大写；vector 路由 =
 * 小写）—— 统一归一小写（review P1-3：大小写失配曾使 blocked/busy 分支
 * 成为死代码）。
 */
export function mixedExportErrorCode(err: unknown): string | null {
  const body = (err as { body?: { detail?: unknown } } | null | undefined)?.body;
  const detail = body?.detail;
  if (
    detail &&
    typeof detail === 'object' &&
    typeof (detail as { code?: unknown }).code === 'string'
  ) {
    return (detail as { code: string }).code.toLowerCase();
  }
  if (typeof detail === 'string' && detail) return detail.toLowerCase();
  const status = (err as { status?: number } | null | undefined)?.status;
  if (status === 503) return 'png_rasterizer_unavailable';
  return null;
}
