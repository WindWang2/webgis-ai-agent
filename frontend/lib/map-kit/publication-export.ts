/**
 * C14：publication vector PDF 导出运行面（前端入口 —— 路由文档化的
 * 「backend-only 待接线」交付）。
 *
 * 与客户端栅格 PDF 链（exporter.ts）的差异：
 * - 不做 canvas 合成 —— 服务端 publication 链从 committed MapSpec 编译
 *   （WeasyPrint 真矢量文本），页面版面 = PublicationIR 单一模型。
 * - spec 组装与 live 同源：composeExportSpec（committed ⊕ pending 展示态）
 *   后 injectResolvedRefSources 内联 ref 载体源（服务端安全决策不做水合；
 *   未解析 ref 保持原样 → 服务端 400 mapspec_ref_sources_unhydrated
 *   typed 拒绝，诚实失败不伪成功）。
 * - 回退三角：503 vector_pdf_unavailable → 自动回退客户端栅格 PDF 并
 *   披露；429 vector_pdf_busy → 结构化 busy 提示；400 → typed code 透传。
 */
import { API_BASE } from '@/lib/api/config';
import {
  exportVectorPdf,
  vectorPdfErrorCode,
  type AtlasRequestPolicy,
  type VectorPdfExportResult,
} from '@/lib/api/publication';

export interface VectorPdfExportRequest {
  title?: string;
  subtitle?: string;
  author?: string;
  dpi?: number;
  atlas?: {
    driver: 'frames' | 'category' | 'feature';
    layerId?: string;
    categoryProperty?: string;
    featuresPerPage?: number;
    pageBudget?: number;
    includeCover?: boolean;
    atlasTitle?: string;
  };
}

export interface VectorPdfExportOutcome {
  ok: boolean;
  error?: string;
  code?: string | null;
  result?: VectorPdfExportResult;
  fellBackToRaster?: boolean;
}

export function atlasPolicyFromRequest(
  atlas: VectorPdfExportRequest['atlas'],
): AtlasRequestPolicy | undefined {
  if (!atlas) return undefined;
  return {
    driver: atlas.driver,
    ...(atlas.layerId ? { layerId: atlas.layerId } : {}),
    ...(atlas.categoryProperty ? { categoryProperty: atlas.categoryProperty } : {}),
    ...(typeof atlas.featuresPerPage === 'number'
      ? { featuresPerPage: atlas.featuresPerPage }
      : {}),
    ...(typeof atlas.pageBudget === 'number' ? { pageBudget: atlas.pageBudget } : {}),
    includeCover: atlas.includeCover ?? false,
    ...(atlas.atlasTitle ? { atlasTitle: atlas.atlasTitle } : {}),
  };
}

/** 组装并提交（getHudState/map 注入与 export_map 命令同形；便于测试桩替换）。 */
export async function runVectorPdfExport(
  getHudState: () => Record<string, unknown> & {
    setPendingSystemMessage: (msg: string | null) => void;
    addExport: (item: {
      id: string;
      name: string;
      type: string;
      size: string;
      date: string;
      filename?: string;
    }) => void;
  },
  map: unknown,
  req: VectorPdfExportRequest,
): Promise<VectorPdfExportOutcome> {
  let spec: Record<string, unknown> | null = null;
  let sessionId: string | undefined;
  try {
    const cursor = await import('@/lib/mapspec/session-cursor');
    const committedRaw = cursor.getCommittedMapSpec();
    if (!committedRaw) {
      return { ok: false, error: 'no_mapspec' };
    }
    const { composeExportSpec } = await import('@/lib/map-kit/exporter');
    const { injectResolvedRefSources } = await import('@/lib/mapspec/ref-source-resolver');
    const hud = getHudState();
    const composed = (await composeExportSpec(
      committedRaw,
      hud as unknown as Parameters<typeof composeExportSpec>[1],
      cursor.getPendingPresentation(),
      cursor.getPendingRemoved(),
    )) as Record<string, unknown> | null;
    if (!composed) {
      return { ok: false, error: 'no_mapspec' };
    }
    spec = injectResolvedRefSources(
      composed as never,
      null,
    ) as unknown as Record<string, unknown>;
    sessionId = cursor.getMapSpecSessionCursor().sessionId;
  } catch {
    return { ok: false, error: 'spec_unavailable' };
  }

  try {
    const result = await exportVectorPdf({
      mapspec: spec,
      title: req.title,
      targetDpi: req.dpi,
      sessionId,
      atlas: atlasPolicyFromRequest(req.atlas),
    });
    recordVectorExport(getHudState, req, result);
    const pages = result.pages ?? 1;
    const atlasNote = result.atlas
      ? `（atlas ${result.atlas_pages?.length ?? pages} 页：封面/目录由 PublicationIR 确定性生成）`
      : '';
    const degradationNote = formatVectorDiagnostics(result);
    getHudState().setPendingSystemMessage(
      `[系统通知] 矢量 PDF \`${req.title || '未命名'}\` 已成功生成` +
        `（${pages} 页，文本可选中检索，PublicationIR ${result.layout_version ?? ''}）` +
        `${atlasNote}，文件已落盘：[下载PDF](${API_BASE}${result.url})。` +
        degradationNote +
        `注意展示完链接后直接结束。`,
    );
    return { ok: true, result };
  } catch (err) {
    const code = vectorPdfErrorCode(err);
    if (code === 'vector_pdf_unavailable') {
      return await fallbackToRasterPdf(getHudState, map, req);
    }
    if (code === 'vector_pdf_busy') {
      getHudState().setPendingSystemMessage(
        '[系统通知] 矢量 PDF 渲染器占用中（服务端串行渲染槽位饱和），请稍后重试。',
      );
      return { ok: false, error: 'vector_pdf_busy', code };
    }
    getHudState().setPendingSystemMessage(
      `[系统通知] 矢量 PDF 导出失败${code ? `（${code}）` : ''}。` +
        (code === 'mapspec_ref_sources_unhydrated'
          ? '存在未内联的 ref 载体源 —— 请等待图层加载完成后重试，或改用栅格导出。'
          : '请向用户致歉并结束流程。'),
    );
    return { ok: false, error: 'export_failed', code };
  }
}

function formatVectorDiagnostics(result: VectorPdfExportResult): string {
  const diags = (result.render_diagnostics ?? []).filter(
    (d) => d && typeof d === 'object' && 'code' in d,
  );
  if (!diags.length) return '';
  const listed = diags.slice(0, 8);
  const omitted = diags.length - listed.length;
  return (
    ` 注意：本次导出存在降级/披露（共 ${diags.length} 条` +
    (omitted > 0 ? `，此处仅列前 ${listed.length} 条` : '') +
    '）：' +
    listed
      .map((d) => {
        const code = String((d as { code?: unknown }).code ?? '');
        const detail = (d as { detail?: unknown }).detail;
        return `${code}${detail ? `（${String(detail)}）` : ''}`;
      })
      .join('、') +
    '。请如实告知用户。'
  );
}

function recordVectorExport(
  getHudState: Parameters<typeof runVectorPdfExport>[0],
  req: VectorPdfExportRequest,
  result: VectorPdfExportResult,
): void {
  getHudState().addExport({
    id: `export-${Date.now()}`,
    name: req.title || '未命名',
    filename: result.filename,
    type: 'pdf',
    size: `${Math.max(1, Math.round((result.pages ?? 1) * 48))}KB~`,
    date: new Date().toLocaleString(),
  });
}

/** 503 契约：回退客户端栅格 PDF（既有引擎链）并披露 vector_pdf_unavailable。 */
async function fallbackToRasterPdf(
  getHudState: Parameters<typeof runVectorPdfExport>[0],
  map: unknown,
  req: VectorPdfExportRequest,
): Promise<VectorPdfExportOutcome> {
  getHudState().setPendingSystemMessage(
    '[系统通知] 矢量 PDF 引擎不可用，已自动回退栅格 PDF 导出（vector_pdf_unavailable）。',
  );
  try {
    const { MapExporterEngine } = await import('@/lib/map-kit/exporter');
    const outcome = await MapExporterEngine.export(
      { map: map as never, getHudState, idleTimeoutMs: 0 },
      {
        format: 'pdf',
        title: req.title,
        subtitle: req.subtitle,
        dpi: req.dpi,
      } as never,
    );
    return {
      ok: outcome.ok,
      error: outcome.ok ? undefined : 'fallback_failed',
      code: 'vector_pdf_unavailable',
      fellBackToRaster: true,
    };
  } catch {
    return {
      ok: false,
      error: 'fallback_failed',
      code: 'vector_pdf_unavailable',
      fellBackToRaster: true,
    };
  }
}
