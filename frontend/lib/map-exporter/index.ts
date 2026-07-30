import { API_BASE } from '@/lib/api/config';
import * as exporter from '@/lib/map-kit/exporter';
import { devOnly } from '@/lib/utils/logger';

// ── Public types ────────────────────────────────────────────────────

/**
 * What the caller wants to export. Mirrors the params the AI (or Map Studio
 * tab) passes in the `export_map` command payload.
 */
export interface ExportRequest {
  title?: string;
  subtitle?: string;
  author?: string;
  dataSource?: string;
  format?: string;        // 'png' | 'svg' | 'pdf'
  paperSize?: string;     // 'screen' | 'A4' | 'A3'
  orientation?: string;   // 'landscape' | 'portrait'
  dpi?: number;
  showLegend?: boolean;
  showCompass?: boolean;
  showScale?: boolean;
  showMetadata?: boolean;
  showGraticules?: boolean;
  showWatermark?: boolean;
  /** Legacy alias used by some AI payloads. */
  include_legend?: boolean;
  /** Legacy alias used by some AI payloads. */
  include_compass?: boolean;
  /** Legacy alias used by some AI payloads. */
  include_scale?: boolean;
}

/**
 * Runtime dependencies injected by the command entry — the only things that
 * vary per call site. Module-level imports (`API_BASE`, `exporter.*`) are
 * imported directly; they don't belong in deps.
 */
export interface ExportDeps {
  /** The MapLibre GL map instance. */
  map: any;
  /** Zustand store accessor (`() => useHudStore.getState()`). */
  getHudState: () => any;
}

/**
 * Structured result returned to the caller so it has visibility into what
 * happened (success URL, failure reason) without parsing side-effects.
 */
export interface ExportOutcome {
  ok: boolean;
  format: string;
  url?: string;
  filename?: string;
  error?: string;
}

// ── Internal helpers ────────────────────────────────────────────────

interface LegendData {
  legendSpec: any | undefined;
  thematicLayer: any | undefined;
  heatmapLegend: { name?: string } | undefined;
}

/**
 * Scan the store's visible layers for legend / thematic / heatmap info.
 * Extracted so it's independently testable and readable.
 */
function discoverLegendData(layers: any[]): LegendData {
  const legendLayer = layers.find(
    (l: any) => l.visible && l.legend_spec,
  );

  const thematicLayerInfo = layers.find(
    (l: any) =>
      l.visible &&
      ((l.style as any)?.type === 'choropleth' ||
        (l.style as any)?.type === 'lisa' ||
        (l.source as any)?.metadata?.thematic_type === 'choropleth'),
  );

  const heatmapLayer = layers.find(
    (l: any) => l.visible && l.type === 'heatmap',
  );

  return {
    legendSpec: legendLayer?.legend_spec,
    thematicLayer: (thematicLayerInfo?.style as any)?.type
      ? thematicLayerInfo?.style
      : thematicLayerInfo,
    heatmapLegend: heatmapLayer ? { name: heatmapLayer.name } : undefined,
  };
}

/**
 * POST the export blob to `/api/v1/export`.
 */
async function uploadExport(
  blob: Blob,
  filename: string,
  title?: string,
): Promise<{ url: string; filename: string }> {
  const form = new FormData();
  form.append('file', blob, filename);
  if (title) form.append('title', title);

  const res = await fetch(`${API_BASE}/api/v1/export`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    throw new Error(`Export upload failed: ${res.status}`);
  }
  const data = await res.json();
  return { url: data.url as string, filename: data.filename as string };
}

/**
 * Record the export in the HUD store.
 */
function recordExport(
  getHudState: () => any,
  name: string,
  filename: string,
  type: string,
  sizeBytes: number,
) {
  getHudState().addExport({
    id: `export-${Date.now()}`,
    name: name || '未命名',
    filename,
    type,
    size: `${(sizeBytes / 1024).toFixed(0)}KB`,
    date: new Date().toLocaleString(),
  });
}

/**
 * Wrap a rendered PNG inside an SVG container so downstream vector tools
 * (Illustrator / Inkscape) can layer vector annotations on the raster base.
 */
function buildSvgWrapper(
  canvas: HTMLCanvasElement,
  title: string,
  dataUrl: string,
): Blob {
  const w = canvas.width;
  const h = canvas.height;
  const safeTitle = (title || 'map').replace(/[<>&]/g, '');
  const svg =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ` +
    `width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<title>${safeTitle}</title>` +
    `<image width="${w}" height="${h}" xlink:href="${dataUrl}"/>` +
    `</svg>`;
  return new Blob([svg], { type: 'image/svg+xml' });
}

// ── Format-specific exporters ───────────────────────────────────────

async function exportPng(
  canvas: HTMLCanvasElement,
  dataUrl: string,
  title: string,
  getHudState: () => any,
): Promise<ExportOutcome> {
  const res = await fetch(dataUrl);
  const blob = await res.blob();
  const upload = await uploadExport(blob, 'export.png', title);
  recordExport(getHudState, title, upload.filename, 'png', blob.size);

  getHudState().setPendingSystemMessage(
    `[系统通知] 专题地图 \`${title || '未命名'}\` 已成功排版合成，` +
      `文件已落盘并分配URL：${upload.url}。 请利用Markdown的图片语法 \`![地图](${API_BASE}${upload.url})\` 将该成品展示给用户，并祝其研究顺利！注意展示完图片后直接结束。`,
  );

  return { ok: true, format: 'png', url: upload.url, filename: upload.filename };
}

async function exportSvg(
  canvas: HTMLCanvasElement,
  dataUrl: string,
  title: string,
  getHudState: () => any,
): Promise<ExportOutcome> {
  const svgBlob = buildSvgWrapper(canvas, title, dataUrl);
  const upload = await uploadExport(svgBlob, 'export.svg', title);
  recordExport(getHudState, title, upload.filename, 'svg', svgBlob.size);

  getHudState().setPendingSystemMessage(
    `[系统通知] 专题地图 SVG \`${title || '未命名'}\` 已成功生成 (含嵌入位图)，` +
      `文件已落盘并分配URL：${upload.url}。可通过以下链接下载：[下载SVG](${API_BASE}${upload.url})。注意展示完链接后直接结束。`,
  );

  return { ok: true, format: 'svg', url: upload.url, filename: upload.filename };
}

async function exportPdf(
  canvas: HTMLCanvasElement,
  title: string,
  subtitle: string | undefined,
  paperSize: string,
  orientation: string,
  author: string,
  dataSource: string,
  getHudState: () => any,
): Promise<ExportOutcome> {
  const pdfBlob = await exporter.exportToPDF(canvas, title || '', subtitle, {
    paperSize: (paperSize === 'A3' ? 'A3' : 'A4') as 'A4' | 'A3',
    orientation: orientation as 'landscape' | 'portrait',
    author,
    dataSource,
  });

  const upload = await uploadExport(pdfBlob, 'export.pdf', title);
  recordExport(getHudState, title, upload.filename, 'pdf', pdfBlob.size);

  getHudState().setPendingSystemMessage(
    `[系统通知] 专题底图 PDF \`${title || '未命名'}\` 已成功生成 (jsPDF 向量版)，` +
      `文件已落盘并分配URL：${upload.url}。` +
      `请告知用户 PDF 已就绪，可通过以下链接下载：[下载PDF](${API_BASE}${upload.url})。注意展示完链接后直接结束。`,
  );

  return { ok: true, format: 'pdf', url: upload.url, filename: upload.filename };
}

// ── Public interface ────────────────────────────────────────────────

/**
 * Run the full map export pipeline.
 *
 * Owns: DPI management, canvas preparation, layout composition, store
 * discovery, format branching (PNG / SVG / PDF), upload, export record,
 * system messages, and error handling.
 *
 * The command entry is responsible for: deferred pop, re-entrancy guard,
 * `map.once('render')` lifecycle, and `map.triggerRepaint()`.
 *
 * @param deps  Runtime dependencies (map instance + store accessor).
 * @param req   Export request params (from the AI or Map Studio tab).
 * @returns     A structured outcome indicating success/failure.
 */
export async function runExport(
  deps: ExportDeps,
  req: ExportRequest,
): Promise<ExportOutcome> {
  const { map, getHudState } = deps;
  const {
    title = '',
    subtitle,
    author = '',
    dataSource = '',
    showWatermark = true,
    showLegend = (req.showLegend ?? req.include_legend ?? true) as boolean,
    showCompass = (req.showCompass ?? req.include_compass ?? true) as boolean,
    showScale = (req.showScale ?? req.include_scale ?? true) as boolean,
    showMetadata = true,
    showGraticules = false,
    format = 'png',
    paperSize = 'screen',
    orientation = 'landscape',
    dpi = 96,
  } = req || {};

  // Surface a loading message — export can take seconds over slow networks
  try {
    getHudState().setPendingSystemMessage(
      `[系统通知] 正在生成 ${String(format).toUpperCase()} 导出文件…`,
    );
  } catch {
    /* defensive */
  }

  const theme = getHudState().theme;

  // High-DPI: set MapLibre pixel ratio for true resolution capture
  const origPixelRatio = map.getPixelRatio();
  const targetPixelRatio = dpi / 96;
  try {
    if (targetPixelRatio > 1) {
      map.setPixelRatio(targetPixelRatio);
      // Wait for MapLibre to re-render at new resolution
      await new Promise<void>((resolve) =>
        map.once('idle', () => resolve()),
      );
    }

    const baseCanvas = map.getCanvas();
    // Canvas is now at target DPI, so pass dpi=96 to avoid double-scaling
    const { canvas: exportCanvas } = exporter.prepareExportCanvas(
      baseCanvas,
      {
        paperSize: paperSize as any,
        orientation: orientation as any,
        dpi: 96,
      },
    );

    const storeState = getHudState();
    const { legendSpec, thematicLayer, heatmapLegend } = discoverLegendData(
      storeState.layers,
    );

    exporter.composeLayout(exportCanvas, title || '', subtitle || '', {
      dpi,
      theme,
      showScale,
      showCompass,
      showWatermark,
      showLegend,
      showMetadata,
      showGraticules,
      author,
      dataSource,
      mapCenter: map.getCenter(),
      mapZoom: map.getZoom(),
      mapBearing: map.getBearing(),
      thematicLayer,
      legendSpec,
      heatmapLegend,
    });

    const dataUrl = exportCanvas.toDataURL('image/png');
    const fmt = (format ?? 'png').toLowerCase();

    if (fmt === 'svg') {
      return await exportSvg(exportCanvas, dataUrl, title, getHudState);
    } else if (fmt === 'pdf') {
      return await exportPdf(
        exportCanvas, title, subtitle, paperSize, orientation,
        author, dataSource, getHudState,
      );
    } else {
      return await exportPng(exportCanvas, dataUrl, title, getHudState);
    }
  } catch (e) {
    devOnly.error('[MapExporter] Canvas extraction/export failed', e);
    const errorMsg = e instanceof Error ? e.message : String(e);
    getHudState().setPendingSystemMessage(
      `[系统通知] 专题地图排版合成失败。错误原因: ${e}。请向用户致歉并结束流程。`,
    );
    return { ok: false, format: (format ?? 'png').toLowerCase(), error: errorMsg };
  } finally {
    // Restore original pixel ratio (even on error)
    if (targetPixelRatio > 1) {
      map.setPixelRatio(origPixelRatio);
    }
  }
}
