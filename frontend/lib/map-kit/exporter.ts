import type { Map } from 'maplibre-gl';
import type { LegendSpec } from './types';
import { resolveStyle, type LayoutStyle } from './layout-style';
import {
  buildExportChrome,
  drawChromeAnnotation,
  drawChromeAttribution,
  drawChromeChartPanel,
  drawChromeColorbar,
  drawChromeDisclosurePanel,
  drawChromeInset,
  drawChromeLegend,
  drawChromeMapBorder,
  drawChromeNorthArrow,
  drawChromeScaleBar,
  drawChromeStatsPanel,
  drawChromeTable,
  drawChromeText,
  type ExportChromeElement,
  type ExportChromeModel,
  type ExportDegradation,
} from './export-chrome';
import { DEFAULT_STACK_STEP_PX } from '@/lib/map-components/resolve-layout';
export type { ExportChromeModel } from './export-chrome';
import { API_BASE } from '@/lib/api/config';
import { apiFetch, isApiError } from '@/lib/api/transport';
import { devOnly } from '@/lib/utils/logger';
import { hydrateMvtLayers } from '@/lib/store/layer-data';
import { getComparisonExport } from '@/lib/map/comparison-export-registry';
import { metersPerPixelAt } from './meters-per-pixel';
import type { ExportFrame, FrameLayout } from './frame-composer';
export type { ExportFrame, ExportFrameWhere, FrameLayout } from './frame-composer';
import {
  graticuleIntervalForZoom,
  graticuleLngLines,
  graticuleLatLines,
} from './graticule-math';
// Re-export the shared oversample helper so existing callers importing from
// './exporter' keep working, while the single source of truth lives in
// ./oversample (shared with the MapSpec-to-SVG compiler).
export { getOversampledZoom, computeOversampleBoost } from './oversample';

/**
 * Captures the current map canvas and returns it as a Blob.
 * @param map The MapLibre map instance.
 * @returns A promise resolving to a PNG Blob.
 */
export async function captureMapCanvas(map: Map): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const canvas = map.getCanvas();
    // Using image/png by default for better quality
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error('Failed to capture map canvas'));
      }
    }, 'image/png');
  });
}

export interface ExportOptions {
  paperSize?: 'screen' | 'A4' | 'A3';
  orientation?: 'landscape' | 'portrait';
  dpi?: number;
}

/**
 * 审计 F33：composeLayout 的完整 options 类型。之前用 any，让未来 caller
 * 传部分字段时静默产生 NaN 渲染（如 mapCenter undefined -> NaN scale bar）。
 *
 * 审计 follow-up（CI Docker build）：初版漏了 legendSpec / heatmapLegend ——
 * map-action-handler.tsx 调用方传这两个字段，TS 报 "Object literal may only
 * specify known properties"。补全字段类型。
 */
export interface ComposeLayoutOptions {
  dpi?: number;
  theme?: 'light' | 'dark';
  showScale?: boolean;
  showCompass?: boolean;
  showWatermark?: boolean;
  showLegend?: boolean;
  showMetadata?: boolean;
  showGraticules?: boolean;
  author?: string;
  dataSource?: string;
  mapCenter?: { lat: number; lng: number };
  mapZoom?: number;
  mapBearing?: number;
  thematicLayer?: unknown;
  /**
   * #802: 画布设备像素 / 逻辑(CSS)像素比。导出画布在默认 dpi=96 路径下是
   * 浏览器原生 backing store（css·devicePixelRatio），与 dpi/96 无关 ——
   * 比例尺长度与经纬网范围必须按真实比值换算，否则 HiDPI 上条长错 dpr 倍。
   * 缺省回退 dpi/96 保持既有调用方语义。
   */
  pixelsPerLogicalPx?: number;
  /** Structured legend spec from layer.legend_spec (graduated/continuous/categorical/divergent). */
  legendSpec?: LegendSpec;
  /** Heatmap gradient legend metadata; consumed when type === 'heatmap' layers are visible. */
  heatmapLegend?: { name?: string; paletteColors?: string[] };
  /** Layout template style overrides (colors, fonts, margins, graticule, watermark). */
  style?: LayoutStyle;
  /**
   * ADR-0081：spec 驱动的 chrome 模型（placement/anchor 语义
   * 来自 resolveMapComponents —— live/export 共用解析层）。在场且 fromSpec
   * 时，title/subtitle/罗盘/比例尺/图例/色条/署名/浮动面板全部按模型槽位
   * 绘制；缺席时保持 legacy 固定槽（旧会话行为不变）。
   */
  chrome?: ExportChromeModel;
  /**
   * W6（ADR-0118）：PDF 矢量文本层专用 —— 标题/副标题改由 PDF doc.text 承载
   * （单一事实源）时，画布两侧（chrome/legacy 路径）都不再画 title/subtitle
   *（连带顶部渐变带），消除双重标题。缺省 false（PNG/SVG 行为不变）。
   */
  skipTitle?: boolean;
}

/**
 * Prepares a new canvas for export, handling cropping and high-DPI upscaling.
 */
export function prepareExportCanvas(
  sourceCanvas: HTMLCanvasElement,
  options: ExportOptions = {}
): { canvas: HTMLCanvasElement; scaleX: number; scaleY: number; srcX: number; srcY: number; srcW: number; srcH: number } {
  const { paperSize = 'screen', orientation = 'landscape', dpi = 96 } = options;
  
  let srcW = sourceCanvas.width;
  let srcH = sourceCanvas.height;
  let srcX = 0;
  let srcY = 0;

  // 1. Calculate Crop Box if A4
  if (paperSize === 'A4' || paperSize === 'A3') {
    const targetRatio = orientation === 'landscape' ? 1.414 : 1 / 1.414;
    const canvasRatio = srcW / srcH;
    
    if (canvasRatio > targetRatio) {
      const newW = srcH * targetRatio;
      srcX = (srcW - newW) / 2;
      srcW = newW;
    } else {
      const newH = srcW / targetRatio;
      srcY = (srcH - newH) / 2;
      srcH = newH;
    }
  }

  // 2. High-DPI Upscaling calculation
  const dpiMultiplier = dpi / 96;
  const targetW = Math.round(srcW * dpiMultiplier);
  const targetH = Math.round(srcH * dpiMultiplier);

  const exportCanvas = document.createElement("canvas");
  exportCanvas.width = targetW;
  exportCanvas.height = targetH;
  const ctx = exportCanvas.getContext("2d");
  if (!ctx) throw new Error("Could not get canvas context");

  // Draw cropped base map
  ctx.drawImage(sourceCanvas, srcX, srcY, srcW, srcH, 0, 0, targetW, targetH);

  return {
    canvas: exportCanvas,
    scaleX: dpiMultiplier,
    scaleY: dpiMultiplier,
    srcX,
    srcY,
    srcW,
    srcH
  };
}

/**
 * Composes a professional map layout with title, subtitle, scale bar, and compass.
 * @param canvas The canvas element to draw on (containing the map image).
 * @param title The map title.
 * @param subtitle Optional subtitle.
 * @param options Configuration options (dpi, theme, showScale, showCompass, etc.)
 */
export function composeLayout(
  canvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: ComposeLayoutOptions = {}
) {
  const ctx = canvas.getContext('2d');
  // FE-21：之前 !ctx 时静默 return，调用方不检查返回值直接 toDataURL →
  // 上传空白画布然后报"导出成功"。改为抛异常让调用方的 catch 处理。
  if (!ctx) throw new Error('Failed to get 2d context for export canvas');

  const {
    dpi = 96,
    theme = 'light',
    showScale = true,
    showCompass = true,
    showWatermark = true,
    showLegend = true,
    showMetadata = true,
    showGraticules = false,
    author = '',
    dataSource = '',
    mapCenter,
    mapZoom,
    mapBearing = 0,
    thematicLayer,
    pixelsPerLogicalPx,
  } = options;

  const dark_mode = theme === 'dark';
  const layoutStyle = resolveStyle(theme, options.style);
  const dpiMultiplier = dpi / 96;
  // #802: 每逻辑像素的真实设备像素数 —— 未显式提供时回退 dpi/96（旧语义）
  const pxPerLogical = pixelsPerLogicalPx ?? dpiMultiplier;
  const scalePx = (val: number) => val * dpiMultiplier;
  const targetW = canvas.width;
  const targetH = canvas.height;
  const marginX = scalePx(layoutStyle.marginPx);

  // ADR-0081：spec chrome 路径 —— placement/anchor 语义来自 MapSpec 组件
  // （live/export 共用 resolveMapComponents），替代固定槽。legacy 路径仅在
  // 无 spec 组件（chrome.fromSpec=false / 未传）时保留。
  const chrome = options.chrome;
  if (chrome?.fromSpec) {
    const d = {
      ctx, darkMode: dark_mode, scalePx, targetW, targetH, style: layoutStyle,
    };
    // margin 一律经 scalePx（review P0：垂直 margin 未缩放，dpi>96 时相对
    // 收缩）；anchorOrigin 的 y 语义 = 距所属边的 margin 距离。
    const mTopTitle = scalePx(52);
    const mTopSub = scalePx(82);
    const mBottom = scalePx(52);
    const mCompass = scalePx(64);
    const mLegend = scalePx(56);
    const mPanel = scalePx(90);
    const mAttr = scalePx(22);

    // ADR-0084（E-1）：槽内堆叠偏移 —— stackIndex 来自与 live 同一求解器
    // （scale_bar 贴边、其余按 priority 远离边；此前导出无堆叠，scale_bar
    // 与 continuous_colorbar 同锚 bottom-right 互相遮挡）。marginY 语义是
    // 距所属边的距离，top/bottom 槽的远离边方向天然由同一偏移承载。
    const stackOffset = (el: ExportChromeElement | undefined, base: number): number =>
      (el?.slotSize ?? 0) > 1 ? base + (el?.stackIndex ?? 0) * scalePx(DEFAULT_STACK_STEP_PX) : base;

    // W6：PDF 矢量文本层时画布不画标题（单一事实源）—— chrome 路径的
    // 标题来自 chromeModel（非入参），必须连同顶部渐变带一起跳过。
    // 1. Header gradient（无浮动 title 时保持顶部渐变；浮动 title 自带面板底）
    if (!options.skipTitle) {
      const headerText = chrome.title && !chrome.title.rect;
      if (headerText) {
        const headerH = chrome.subtitle?.text ? scalePx(130) : scalePx(100);
        const headerGrad = ctx.createLinearGradient(0, 0, 0, headerH);
        headerGrad.addColorStop(0, dark_mode ? "rgba(0,10,20,0.88)" : "rgba(255,255,255,0.96)");
        headerGrad.addColorStop(0.65, dark_mode ? "rgba(0,10,20,0.45)" : "rgba(255,255,255,0.55)");
        headerGrad.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = headerGrad;
        ctx.fillRect(0, 0, targetW, headerH);
      }

      // 2. Title / subtitle（anchor 对齐 —— top-center 居中，与 live 一致）
      if (chrome.title?.text) {
        drawChromeText(d, chrome.title, 32, layoutStyle.titleColor, { marginX, marginY: stackOffset(chrome.title, mTopTitle) });
      }
      if (chrome.subtitle?.text) {
        drawChromeText(
          d, chrome.subtitle, 20,
          dark_mode ? "rgba(255,255,255,0.72)" : "rgba(30,41,59,0.72)",
          { marginX, marginY: stackOffset(chrome.subtitle, mTopSub) },
        );
      }
    }

    // 3. Scale bar（anchor 槽位 —— bottom-right 缺省，与 live 一致）
    if (chrome.scaleBar && showScale && mapCenter && mapZoom !== undefined) {
      const metersPerPx = metersPerPixelAt(mapZoom, mapCenter.lat);
      drawChromeScaleBar(d, chrome.scaleBar, metersPerPx, pxPerLogical, { marginX, marginY: stackOffset(chrome.scaleBar, mBottom) });
    }

    // 4. Compass（旋转符号与 live 对齐：-bearing）
    if (chrome.northArrow && showCompass) {
      drawChromeNorthArrow(d, chrome.northArrow, mapBearing, { marginX, marginY: stackOffset(chrome.northArrow, mCompass) });
    }

    // 4.5 Graticule（P6：请求参数 **或** spec graticule 组件 enabled ——
    // 组件通道与请求通道同一条绘制路径，不建第二算法）
    if (
      (showGraticules || chrome.graticuleEnabled) &&
      mapCenter && mapZoom !== undefined
    ) {
      _drawGraticules(ctx, { dark_mode, scalePx, targetW, targetH, mapCenter, mapZoom, graticuleColor: layoutStyle.graticuleColor, pxPerLogical });
    }

    // 4.6 Map Border（P6：全画布图框；描边在 chrome 文本之下、栅格之上）
    if (chrome.border) {
      drawChromeMapBorder(d, chrome.border);
    }

    // 5. Legend / colorbar（v2：图例族多实例 —— 每个绑定层独立绘制；
    // 单实例字段 legend/colorbar 与数组首元素相同，向后兼容旧消费者）
    if (showLegend) {
      const legendEls = chrome.legends.length
        ? chrome.legends
        : chrome.legend
          ? [chrome.legend]
          : [];
      for (const el of legendEls) {
        drawChromeLegend(d, el, { marginX, marginY: stackOffset(el, mLegend) });
      }
      if (legendEls.length === 0 && options.heatmapLegend) {
        // 热力图无量化色条（legend_spec 缺 min/max）时回落定性渐变图例 ——
        // review P1：不能让热力图-only 成品完全丢图例。
        _drawHeatmapLegend(
          { ctx, dark_mode, scalePx, targetW, targetH },
          options.heatmapLegend.name,
          0,
          options.heatmapLegend.paletteColors,
        );
      }
    }
    if (showLegend) {
      const colorbarEls = chrome.colorbars.length
        ? chrome.colorbars
        : chrome.colorbar
          ? [chrome.colorbar]
          : [];
      for (const el of colorbarEls) {
        drawChromeColorbar(d, el, { marginX, marginY: stackOffset(el, mLegend) });
      }
    }

    // 5.4 Inset maps（v2：区位插图 —— 纯 SVG 投影语义同链；bounds 缺省
    // 由 insetMainBbox 携带，无指示范围只画范围示意；槽内堆叠与
    // north_arrow 同侧时经 stackOffset 让位 —— 与 live topSlotIndexes 同语义）
    for (const inset of chrome.insets) {
      drawChromeInset(d, inset, { marginX, marginY: stackOffset(inset, scalePx(12)) });
    }

    // 5.5 浮动面板（statistics/chart/annotation —— 终审 F1：注释卡导出；
    //     V3：disclosure 族（methodology/uncertainty/decision）同链导出；
    //     V4：table_panel 有界快照导出）
    for (const panel of chrome.panels) {
      if (panel.kind === 'statistics') {
        drawChromeStatsPanel(d, panel, { marginX, marginY: stackOffset(panel, mPanel) });
      } else if (panel.kind === 'chart') {
        drawChromeChartPanel(d, panel, { marginX, marginY: stackOffset(panel, mPanel) });
      } else if (panel.kind === 'annotation') {
        drawChromeAnnotation(d, panel, {
          marginX, marginY: stackOffset(panel, mPanel),
          mapCenter, mapZoom, pxPerLogical,
        });
      } else if (panel.kind === 'methodology' || panel.kind === 'uncertainty' || panel.kind === 'decision') {
        drawChromeDisclosurePanel(d, panel, { marginX, marginY: stackOffset(panel, mPanel) });
      } else if (panel.kind === 'table') {
        drawChromeTable(d, panel, { marginX, marginY: stackOffset(panel, mPanel) });
      }
    }

    // 6. Attribution（spec 组件文本；请求 author/dataSource 仍在 metadata 行）
    if (chrome.attribution?.text) {
      drawChromeAttribution(d, chrome.attribution, { marginX, marginY: stackOffset(chrome.attribution, mAttr) });
    }

    // 7. Watermark / metadata（请求驱动，与 legacy 同款）
    _drawWatermarkAndMetadata(ctx, {
      dark_mode, scalePx, targetW, targetH,
      showWatermark, showMetadata, author, dataSource, mapCenter,
      watermarkText: layoutStyle.watermarkText,
    });
    return;
  }

  // W6：PDF 矢量文本层时画布不画标题（与 chrome 路径同一开关）——
  // 标题/副标题/顶部渐变带整体跳过，避免双重标题。
  // 1. Header gradient
  if (!options.skipTitle) {
    const headerH = subtitle ? scalePx(130) : scalePx(100);
    const headerGrad = ctx.createLinearGradient(0, 0, 0, headerH);
    headerGrad.addColorStop(0, dark_mode ? "rgba(0,10,20,0.88)" : "rgba(255,255,255,0.96)");
    headerGrad.addColorStop(0.65, dark_mode ? "rgba(0,10,20,0.45)" : "rgba(255,255,255,0.55)");
    headerGrad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = headerGrad;
    ctx.fillRect(0, 0, targetW, headerH);

    // 2. Title
    ctx.fillStyle = layoutStyle.titleColor;
    ctx.font = layoutStyle.titleFont.includes('px') ? layoutStyle.titleFont : `bold ${scalePx(32)}px ${layoutStyle.fontFamily}`;
    ctx.fillText(title || "WebGIS AI Agent", marginX, scalePx(52));

    if (subtitle) {
      ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.72)" : "rgba(30,41,59,0.72)";
      ctx.font = `${scalePx(20)}px ${layoutStyle.fontFamily}`;
      ctx.fillText(subtitle, marginX, scalePx(82));
    }
  }

  // 3. Scale bar
  if (showScale && mapCenter && mapZoom !== undefined) {
    const metersPerPx = metersPerPixelAt(mapZoom, mapCenter.lat);
    
    const logicalW = targetW / pxPerLogical;
    const targetPx = Math.round(logicalW * 0.12);
    const rawMeters = metersPerPx * targetPx;
    
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawMeters)));
    const nice = [1, 2, 5, 10].reduce((prev, n) => {
      const candidate = n * magnitude;
      return Math.abs(candidate - rawMeters) < Math.abs(prev - rawMeters)
        ? candidate
        : prev;
    }, magnitude);
    
    const barPx = (nice / metersPerPx) * pxPerLogical;
    const barLabel = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;

    const bx = marginX, by = targetH - scalePx(52), bh = scalePx(8);
    ctx.strokeStyle = dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)";
    ctx.lineWidth = scalePx(1.5);
    ctx.strokeRect(bx, by, barPx, bh);
    
    const segCount = 4;
    const segW = barPx / segCount;
    for (let i = 0; i < segCount; i++) {
      ctx.fillStyle =
        i % 2 === 0
          ? dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)"
          : "rgba(0,0,0,0)";
      ctx.fillRect(bx + i * segW, by, segW, bh);
    }
    
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
    ctx.font = `bold ${scalePx(13)}px ${layoutStyle.fontFamily}`;
    ctx.textAlign = "left";
    ctx.fillText("0", bx, by - scalePx(4));
    ctx.textAlign = "right";
    ctx.fillText(barLabel, bx + barPx, by - scalePx(4));
    ctx.textAlign = "left";
  }

  // 4. Compass
  if (showCompass) {
    const bearing = mapBearing;
    const cx = targetW - scalePx(64), cy = scalePx(64), r = scalePx(28);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((bearing * Math.PI) / 180);

    ctx.shadowColor = "rgba(0,0,0,0.4)";
    ctx.shadowBlur = scalePx(6);

    ctx.beginPath();
    ctx.moveTo(0, -r);
    ctx.lineTo(r * 0.35, 0);
    ctx.lineTo(0, r * 0.2);
    ctx.lineTo(-r * 0.35, 0);
    ctx.closePath();
    ctx.fillStyle = layoutStyle.accentColor || "#e53e3e";
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(0, r);
    ctx.lineTo(r * 0.35, 0);
    ctx.lineTo(0, r * 0.2);
    ctx.lineTo(-r * 0.35, 0);
    ctx.closePath();
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.9)" : "#f8fafc";
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.beginPath();
    ctx.arc(0, 0, scalePx(4), 0, 2 * Math.PI);
    ctx.fillStyle = "#1e293b";
    ctx.fill();

    ctx.restore();

    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
    ctx.font = `bold ${scalePx(13)}px ${layoutStyle.fontFamily}`;
    ctx.textAlign = "center";
    ctx.fillText("N", cx, cy - r - scalePx(6));
    ctx.textAlign = "left";
  }

  // 4.5 Graticule / coordinate grid lines
  if (showGraticules && mapCenter && mapZoom !== undefined) {
    _drawGraticules(ctx, { dark_mode, scalePx, targetW, targetH, mapCenter, mapZoom, graticuleColor: layoutStyle.graticuleColor, pxPerLogical });
  }

  // 5. Legend
  const { heatmapLegend, legendSpec } = options;
  if (showLegend && (thematicLayer || heatmapLegend || legendSpec)) {
    _drawLegend(ctx, {
      dark_mode, scalePx, targetW, targetH,
      thematicLayer, heatmapLegend, legendSpec,
    });
  }

  // 6+7. Watermark + metadata（legacy 路径与 chrome 路径共用同一绘制）
  _drawWatermarkAndMetadata(ctx, {
    dark_mode, scalePx, targetW, targetH,
    showWatermark, showMetadata, author, dataSource, mapCenter,
    watermarkText: layoutStyle.watermarkText,
  });
}

/** Watermark + metadata 行（legacy 与 chrome 路径共用同一绘制）。 */
function _drawWatermarkAndMetadata(
  ctx: CanvasRenderingContext2D,
  opts: {
    dark_mode: boolean;
    scalePx: (v: number) => number;
    targetW: number;
    targetH: number;
    showWatermark: boolean;
    showMetadata: boolean;
    author?: string;
    dataSource?: string;
    mapCenter?: { lat: number; lng: number };
    watermarkText?: string;
  },
) {
  const { dark_mode, scalePx, targetW, targetH, showWatermark, showMetadata, author, dataSource, mapCenter, watermarkText } = opts;
  if (showWatermark) {
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)";
    ctx.textAlign = "right";
    ctx.font = `bold ${scalePx(16)} monospace`;
    ctx.fillText(watermarkText ?? "WebGIS AI Agent", targetW - scalePx(36), targetH - scalePx(18));
    ctx.textAlign = "left";
  }
  if (showMetadata) {
    const parts: string[] = [];
    if (author) parts.push(`作者: ${author}`);
    parts.push(`日期: ${new Date().toISOString().slice(0, 10)}`);
    if (mapCenter) parts.push(`CRS: EPSG:4326 (display)`);
    if (dataSource) parts.push(`数据: ${dataSource}`);

    if (parts.length > 0) {
      ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.3)";
      ctx.font = `${scalePx(10)}px sans-serif`;
      ctx.textAlign = "left";
      ctx.fillText(parts.join('  |  '), scalePx(56), targetH - scalePx(18));
      ctx.textAlign = "left";
    }
  }
}

// ── Legend drawing helpers ──────────────────────────────────────────

// ── Graticule drawing ──────────────────────────────────────────────

function _drawGraticules(
  ctx: CanvasRenderingContext2D,
  opts: {
    dark_mode: boolean;
    scalePx: (v: number) => number;
    targetW: number;
    targetH: number;
    mapCenter: { lat: number; lng: number };
    mapZoom: number;
    graticuleColor?: string;
    /** #802: 设备像素/逻辑像素比（缺省 1 —— 调用方已按 dpi 缩放时） */
    pxPerLogical?: number;
  }
) {
  const { dark_mode, scalePx, targetW, targetH, mapCenter, mapZoom, graticuleColor, pxPerLogical = 1 } = opts;

  // P3：间隔表 + zoom 映射 + 吸附抽取为共享模块 graticule-math.ts ——
  // live 渲染器与导出侧单一语义源（ADR-0081 parity）。
  const interval = graticuleIntervalForZoom(mapZoom);

  // Calculate geographic extent from center and zoom (via shared 512-tile helper)
  const metersPerPixel = metersPerPixelAt(mapZoom, mapCenter.lat);
  // #802: 经纬网范围按逻辑(CSS)像素宽度推导 —— 设备像素会随 dpr/dpi 虚增
  const halfWidthMeters = (targetW / pxPerLogical / 2) * metersPerPixel;
  const halfHeightMeters = (targetH / pxPerLogical / 2) * metersPerPixel;

  // Convert meters to degrees (approximate)
  const metersPerDegree = 111319.9;
  const halfWidthDeg = halfWidthMeters / metersPerDegree;
  const halfHeightDeg = halfHeightMeters / (metersPerDegree * Math.cos((mapCenter.lat * Math.PI) / 180));

  const minLng = mapCenter.lng - halfWidthDeg;
  const maxLng = mapCenter.lng + halfWidthDeg;
  const minLat = mapCenter.lat - halfHeightDeg;
  const maxLat = mapCenter.lat + halfHeightDeg;

  // Snap to interval grid
  const lngLines = graticuleLngLines(minLng, maxLng, interval);
  const latLines = graticuleLatLines(minLat, maxLat, interval);

  ctx.save();
  ctx.strokeStyle = graticuleColor || (dark_mode ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.12)");
  ctx.lineWidth = scalePx(0.5);
  ctx.setLineDash([scalePx(4), scalePx(4)]);
  ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.4)" : "rgba(0,0,0,0.35)";
  ctx.font = `${scalePx(9)}px sans-serif`;

  // Draw longitude lines (vertical)
  for (const { value: lng, label } of lngLines) {
    const x = ((lng - minLng) / (maxLng - minLng)) * targetW;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, targetH);
    ctx.stroke();
    // Label at bottom
    ctx.textAlign = "center";
    ctx.fillText(label, x, targetH - scalePx(22));
  }

  // Draw latitude lines (horizontal)
  for (const { value: lat, label } of latLines) {
    const y = targetH - ((lat - minLat) / (maxLat - minLat)) * targetH;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(targetW, y);
    ctx.stroke();
    // Label at left
    ctx.textAlign = "left";
    ctx.fillText(label, scalePx(4), y - scalePx(3));
  }

  ctx.setLineDash([]);
  ctx.restore();
}

const HEATMAP_COLORS = ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'];
const HEATMAP_LABELS = ['极低', '低', '中', '高', '极高'];

interface LegendDrawCtx {
  ctx: CanvasRenderingContext2D;
  dark_mode: boolean;
  scalePx: (v: number) => number;
  targetW: number;
  targetH: number;
}

function _drawLegendBox(ld: LegendDrawCtx, legendW: number, legendH: number, drawContent: (lx: number, ly: number) => void, yOffset: number = 0) {
  const { ctx, scalePx, targetW, targetH, dark_mode } = ld;
  const lx = targetW - legendW - scalePx(56);
  const ly = targetH - legendH - scalePx(56) - yOffset;

  ctx.fillStyle = dark_mode ? "rgba(0,10,20,0.82)" : "rgba(255,255,255,0.88)";
  ctx.beginPath();
  const rad = scalePx(8);
  ctx.moveTo(lx + rad, ly);
  ctx.lineTo(lx + legendW - rad, ly);
  ctx.arcTo(lx + legendW, ly, lx + legendW, ly + rad, rad);
  ctx.lineTo(lx + legendW, ly + legendH - rad);
  ctx.arcTo(lx + legendW, ly + legendH, lx + legendW - rad, ly + legendH, rad);
  ctx.lineTo(lx + rad, ly + legendH);
  ctx.arcTo(lx, ly + legendH, lx, ly + legendH - rad, rad);
  ctx.lineTo(lx, ly + rad);
  ctx.arcTo(lx, ly, lx + rad, ly, rad);
  ctx.closePath();
  ctx.fill();

  drawContent(lx, ly);
}

function _drawHeatmapLegend(
  ld: LegendDrawCtx,
  name?: string,
  yOffset: number = 0,
  paletteColors?: string[],
): number {
  const { ctx, scalePx } = ld;
  // 色带同源：优先热力层 legend_spec.palette_colors（与 live FloatingLegend、
  // 后端 NATIVE_HEATMAP_COLORS 同一色）；缺省回落历史 cyan→red 渐变。
  const colors =
    paletteColors && paletteColors.length >= 2 ? paletteColors : HEATMAP_COLORS;
  const labels =
    colors === HEATMAP_COLORS ? HEATMAP_LABELS : ['低', '', '', '', '高'];
  const padding = scalePx(10);
  const barH = scalePx(8);
  const gradientW = scalePx(140);
  const gradientLabelH = scalePx(16);
  const titleH = name ? scalePx(20) : 0;
  const legendW = padding * 2 + gradientW + scalePx(6);
  const legendH = padding * 2 + titleH + barH + gradientLabelH + scalePx(4);

  _drawLegendBox(ld, legendW, legendH, (lx, ly) => {
    let y = ly + padding;

    if (name) {
      ctx.fillStyle = ld.dark_mode ? "rgba(255,255,255,0.7)" : "rgba(100,116,139,0.9)";
      ctx.font = `${scalePx(10)}px monospace`;
      ctx.fillText(name.toUpperCase(), lx + padding, y + scalePx(12));
      y += scalePx(18);
    }

    // Gradient bar: equal color segments from the shared palette
    const segW = gradientW / colors.length;
    for (let i = 0; i < colors.length; i++) {
      ctx.fillStyle = colors[i];
      ctx.fillRect(lx + padding + i * segW, y, segW + 1, barH);
    }

    // Labels below gradient
    y += barH + scalePx(4);
    ctx.fillStyle = ld.dark_mode ? "rgba(255,255,255,0.6)" : "rgba(100,116,139,0.8)";
    ctx.font = `${scalePx(10)}px sans-serif`;
    ctx.textAlign = "left";
    ctx.fillText(labels[0], lx + padding, y + scalePx(10));
    ctx.textAlign = "right";
    ctx.fillText(labels[labels.length - 1], lx + padding + gradientW, y + scalePx(10));
    ctx.textAlign = "center";
    // 中间刻度按实际色带段数分布；自定义 palette 的中间刻度留空（只有
    // 首/尾语义标注），不再沿用硬编码 cyan→red 的 中/高 文案。
    for (let i = 1; i < labels.length - 1; i++) {
      if (!labels[i]) continue;
      ctx.fillText(labels[i], lx + padding + (i / (labels.length - 1)) * gradientW, y + scalePx(10));
    }
    ctx.textAlign = "left";
  }, yOffset);
  return legendH + scalePx(10);
}

// 与后端 app/lib/cartography/palettes.py 的 COLOR_PALETTES 同源镜像
// （ColorBrewer 官方 hex；语义族登记见后端 model_library.PALETTE_KINDS）。
export const COLOR_PALETTES: Record<string, string[]> = {
  YlOrRd: ["#ffffb2","#fed976","#feb24c","#fd8d3c","#f03b20","#bd0026"],
  Blues:  ["#eff3ff","#bdd7e7","#6baed6","#3182bd","#08519c"],
  Greens: ["#edf8e9","#bae4b3","#74c476","#31a354","#006d2c"],
  Reds:   ["#fee5d9","#fcae91","#fb6a4a","#de2d26","#a50f15"],
  Oranges:["#feedde","#fdbe85","#fd8d3c","#e6550d","#a63603"],
  Purples:["#f2f0f7","#cbc9e2","#9e9ac8","#756bb1","#54278f"],
  RdYlGn: ["#d73027","#fc8d59","#fee08b","#d9ef8b","#91cf60","#1a9850"],
  RdBu:   ["#ca0020","#f4a582","#f7f7f7","#92c5de","#0571b0"],
  Set1:   ["#e41a1c","#377eb8","#4daf4a","#984ea3","#ff7f00","#ffff33","#a65628","#f781bf","#999999"],
  Set2:   ["#66c2a5","#fc8d62","#8da0cb","#e78ac3","#a6d854","#ffd92f","#e5c494","#b3b3b3"],
  Dark2:  ["#1b9e77","#d95f02","#7570b3","#e7298a","#66a61e","#e6ab02","#a6761d","#666666"],
  Pastel1:["#fbb4ae","#b3cde3","#ccebc5","#decbe4","#fed9a6","#ffffcc","#e5d8bd","#fddaec","#f2f2f2"],
  Viridis:["#440154","#3b528b","#21908c","#5dc963","#fde725"],
  Magma:  ["#000004","#3b0f70","#8c2981","#de4968","#feb078","#fcfdbf"],
  Inferno:["#000004","#420a68","#932667","#dd513a","#fca50a","#fcffa4"],
  Plasma: ["#0d0887","#6a00a8","#b12a90","#e16462","#fca636","#f0f921"],
};

function _drawDiscreteLegend(ld: LegendDrawCtx, field: string, colors: string[], labels: string[], yOffset: number = 0): number {
  const { ctx, scalePx } = ld;
  const classes = Math.min(colors.length, labels.length);
  const itemH = scalePx(22), itemW = scalePx(18), padding = scalePx(10), gapX = scalePx(8);

  ctx.font = `${scalePx(11)}px sans-serif`;
  let maxTextW = 0;
  for (const label of labels) {
    maxTextW = Math.max(maxTextW, ctx.measureText(label).width);
  }

  const legendW = padding * 2 + itemW + gapX + maxTextW + scalePx(10);
  const legendH = padding * 2 + scalePx(24) + classes * itemH;

  _drawLegendBox(ld, legendW, legendH, (lx, ly) => {
    ctx.fillStyle = ld.dark_mode ? "#00f2ff" : "#1e293b";
    ctx.font = `bold ${scalePx(12)}px sans-serif`;
    ctx.fillText(`字段: ${field}`, lx + padding, ly + padding + scalePx(12));

    for (let i = 0; i < classes; i++) {
      const iy = ly + padding + scalePx(24) + i * itemH;
      ctx.fillStyle = colors[i];
      ctx.fillRect(lx + padding, iy, itemW, itemH - scalePx(4));
      ctx.strokeStyle = "rgba(128,128,128,0.4)";
      ctx.lineWidth = scalePx(0.5);
      ctx.strokeRect(lx + padding, iy, itemW, itemH - scalePx(4));
      ctx.fillStyle = ld.dark_mode ? "rgba(255,255,255,0.85)" : "#334155";
      ctx.font = `${scalePx(11)}px sans-serif`;
      ctx.fillText(labels[i], lx + padding + itemW + gapX, iy + itemH - scalePx(8));
    }
  }, yOffset);
  return legendH + scalePx(10);
}

function _drawLegend(
  ctx: CanvasRenderingContext2D,
  opts: {
    dark_mode: boolean;
    scalePx: (v: number) => number;
    targetW: number;
    targetH: number;
    thematicLayer?: any;
    heatmapLegend?: { name?: string; paletteColors?: string[] };
    legendSpec?: any;
  }
) {
  const ld: LegendDrawCtx = {
    ctx,
    dark_mode: opts.dark_mode,
    scalePx: opts.scalePx,
    targetW: opts.targetW,
    targetH: opts.targetH,
  };

  let yOffset = 0;

  // Legend 1: LegendSpec from layer.legend_spec (structured, typed)
  if (opts.legendSpec) {
    const spec = opts.legendSpec;
    if (spec.type === 'graduated' || spec.type === 'continuous' || spec.type === 'divergent') {
      const colors = spec.palette_colors || COLOR_PALETTES[spec.palette] || COLOR_PALETTES['YlOrRd'];
      const formatNum = (n: number) =>
        n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` :
        n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` :
        n.toFixed(1);
      const labels: string[] = [];
      if (spec.breaks && spec.breaks.length >= 2) {
        for (let i = 0; i < spec.breaks.length - 1; i++) {
          labels.push(`${formatNum(spec.breaks[i])} – ${formatNum(spec.breaks[i + 1])}`);
        }
      } else if (spec.type === 'continuous' && spec.min !== undefined && spec.max !== undefined) {
        labels.push(formatNum(spec.min));
        labels.push(formatNum((spec.min + spec.max) / 2));
        labels.push(formatNum(spec.max));
        while (labels.length < colors.length) labels.push('');
      }
      yOffset += _drawDiscreteLegend(ld, spec.field || '未知字段', colors, labels, yOffset);
    } else if (spec.type === 'categorical') {
      const colors = (spec.categories || []).map((c: any) => c.color);
      const labels = (spec.categories || []).map((c: any) => c.label || c.key);
      yOffset += _drawDiscreteLegend(ld, spec.field || '未知字段', colors, labels, yOffset);
    }
  }

  // Legend 2: Heatmap gradient legend
  if (opts.heatmapLegend) {
    yOffset += _drawHeatmapLegend(
      ld, opts.heatmapLegend.name, yOffset, opts.heatmapLegend.paletteColors,
    );
  }

  // Legend 3: Legacy thematicLayer (ThematicStyleDef shape)
  if (opts.thematicLayer) {
    const styleDef = opts.thematicLayer as any;
    const field = styleDef.field || '未知字段';
    let colors: string[] = styleDef.colors || [];
    let labels: string[] = styleDef.legend_labels || [];

    const meta = (styleDef.source as any)?.metadata;
    if (meta && meta.breaks && meta.palette) {
      colors = COLOR_PALETTES[meta.palette] ?? COLOR_PALETTES["YlOrRd"];
      const formatNum = (n: number) =>
        n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` :
        n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` :
        n.toFixed(1);
      labels = [];
      for (let i = 0; i < meta.breaks.length - 1; i++) {
        labels.push(`${formatNum(meta.breaks[i])} – ${formatNum(meta.breaks[i + 1])}`);
      }
    }

    if (colors.length > 0 && labels.length > 0) {
      _drawDiscreteLegend(ld, field, colors, labels, yOffset);
    }
  }
}

/**
 * W6：非 WinAnsi 字符检测（code point > U+00FF，含 CJK/emoji 等）——
 * jsPDF 标准 14 字体只编码 WinAnsi，越界字符在 PDF 文本层必然乱码。
 */
export function hasNonWinAnsiChars(s: string): boolean {
  return /[^ -ÿ]/.test(s);
}

/**
 * 页标题随画布栅格化（W9：帧标题含非 WinAnsi 字符时，doc.text 会乱码 ——
 * 复制画布后把标题画上去，诚实降级不伪造矢量）。
 */
function rasterizeTitleOnCanvas(canvas: HTMLCanvasElement, title: string): HTMLCanvasElement {
  const out = document.createElement('canvas');
  out.width = canvas.width;
  out.height = canvas.height;
  const ctx = out.getContext('2d');
  if (!ctx) return out;
  ctx.drawImage(canvas, 0, 0);
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.fillRect(0, 0, out.width, 44);
  ctx.fillStyle = '#1e293b';
  ctx.font = 'bold 24px sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(title.slice(0, 60), 16, 30);
  return out;
}

/**
 * Export the composed canvas as a PDF using jsPDF.
 *
 * W6（ADR-0118）如实语义：地图永远是位图画布嵌入；文本层两种状态 ——
 * 'vector'（缺省）：title/subtitle 以 doc.text 矢量书写（仅 WinAnsi 可编码
 * 字符安全，非 ASCII 会乱码 —— 由调用方先做 pdf_text_rasterized_cjk 判定）；
 * 'skip'：title/subtitle 不进 PDF 文本层（已随画布栅格化）。页脚固定 ASCII
 * 标签（jsPDF 标准字体无法编码 CJK，此前「日期:/作者:」必然乱码）。
 *
 * W9：pages 非空时走多帧图集 —— 首页嵌入 pages[0].canvas（封面，标题带
 * 主标题），其余帧逐页 addPage；页标题 WinAnsi 可编码走 doc.text 矢量，
 * 否则随画布栅格化（onDegradation 回传 pdf_text_rasterized_cjk）。
 */
export async function exportToPDF(
  canvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: {
    paperSize?: 'A4' | 'A3';
    orientation?: 'landscape' | 'portrait';
    author?: string;
    dataSource?: string;
    /** W6：'vector'（缺省）画 title/subtitle；'skip' 跳过（画布已栅格化）。 */
    textLayer?: 'vector' | 'skip';
    /** W9：多帧图集页（canvas + 每帧标题）。 */
    pages?: Array<{ canvas: HTMLCanvasElement; title?: string }>;
    /** W9：PDF 内部诊断回传（页标题栅格化）。 */
    onDegradation?: (d: ExportDegradation) => void;
  } = {}
): Promise<Blob> {
  const { default: jsPDF } = await import('jspdf');
  const { paperSize = 'A4', orientation = 'landscape', author, dataSource } = options;

  const doc = new jsPDF({
    orientation,
    unit: 'mm',
    format: paperSize === 'A3' ? 'a3' : 'a4',
  });

  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 10;

  // Map image area
  const mapTop = 25;
  const mapBottom = 15;
  const mapW = pageW - margin * 2;
  const mapH = pageH - mapTop - mapBottom;
  const mapX = margin;
  const mapY = mapTop;

  // Add map image（W9：pages 在场时首页嵌入 pages[0].canvas —— 封面页）
  const pages = options.pages ?? [];
  const mainCanvas = pages.length > 0 ? pages[0].canvas : canvas;
  const imgData = mainCanvas.toDataURL('image/png');
  // #803: 帧内等比适配 —— 固定帧直接拉伸会畸变地理形状（A4 横版帧 1.63:1
  // 对 1.414 裁剪画布横向拉伸 ×1.15；screen 默认无裁剪时窄画布拉伸可达
  // ×1.86，圆形要素变椭圆）。取帧内最大等比矩形并居中。
  const imgRatio = mainCanvas.width / mainCanvas.height;
  const frameRatio = mapW / mapH;
  let placedW = mapW;
  let placedH = mapH;
  if (imgRatio > frameRatio) {
    placedH = mapW / imgRatio;
  } else {
    placedW = mapH * imgRatio;
  }
  const placedX = mapX + (mapW - placedW) / 2;
  const placedY = mapY + (mapH - placedH) / 2;
  doc.addImage(imgData, 'PNG', placedX, placedY, placedW, placedH);

  // Border around the placed map area
  doc.setDrawColor(200);
  doc.setLineWidth(0.3);
  doc.rect(placedX, placedY, placedW, placedH);

  // Title（W6：textLayer='skip' 时已随画布栅格化 —— 文本层不重复书写）
  const textVector = (options.textLayer ?? 'vector') === 'vector';
  if (textVector) {
    doc.setFontSize(16);
    doc.setTextColor(30, 41, 59);
    doc.text(title || 'WebGIS AI Agent', pageW / 2, 15, { align: 'center' });

    // Subtitle
    if (subtitle) {
      doc.setFontSize(10);
      doc.setTextColor(100, 116, 139);
      doc.text(subtitle, pageW / 2, 21, { align: 'center' });
    }
  }

  // Footer（W6：标签改 ASCII —— jsPDF 标准字体编码不了 CJK，此前「日期:」
  // 等前缀在 PDF 里必然乱码。非 WinAnsi 的 author/dataSource 值从页脚剔除
  //（乱码比缺席更糟；PDF 元数据仍保留原文）。
  const dateStr = new Date().toISOString().slice(0, 10);
  const footerParts = [`Date: ${dateStr}`];
  if (author && !hasNonWinAnsiChars(author)) footerParts.push(`Author: ${author}`);
  if (dataSource && !hasNonWinAnsiChars(dataSource)) footerParts.push(`Data: ${dataSource}`);
  footerParts.push('Generated by WebGIS AI Agent');

  doc.setFontSize(7);
  doc.setTextColor(148, 163, 184);
  doc.text(footerParts.join('  |  '), pageW / 2, pageH - 5, { align: 'center' });

  // W9：附加帧页（atlas）—— 同版式 addPage；页标题 WinAnsi 可编码走
  // doc.text 矢量，否则随画布栅格化（诚实降级 + 显式诊断）。
  for (let i = 1; i < pages.length; i++) {
    const page = pages[i];
    doc.addPage(paperSize === 'A3' ? 'a3' : 'a4', orientation);
    let pageCanvas = page.canvas;
    const pageTitle = page.title || '';
    const pageTitleVector = !pageTitle || !hasNonWinAnsiChars(pageTitle);
    if (pageTitle && !pageTitleVector) {
      pageCanvas = rasterizeTitleOnCanvas(page.canvas, pageTitle);
      options.onDegradation?.({
        code: 'pdf_text_rasterized_cjk',
        detail: `页标题「${pageTitle.slice(0, 20)}」含非 WinAnsi 字符，随画布栅格化`,
      });
    }
    const pImgData = pageCanvas.toDataURL('image/png');
    const pRatio = pageCanvas.width / pageCanvas.height;
    let pW = mapW;
    let pH = mapH;
    if (pRatio > frameRatio) {
      pH = mapW / pRatio;
    } else {
      pW = mapH * pRatio;
    }
    const pX = mapX + (mapW - pW) / 2;
    const pY = mapY + (mapH - pH) / 2;
    doc.addImage(pImgData, 'PNG', pX, pY, pW, pH);
    doc.setDrawColor(200);
    doc.setLineWidth(0.3);
    doc.rect(pX, pY, pW, pH);
    if (pageTitle && pageTitleVector) {
      doc.setFontSize(12);
      doc.setTextColor(30, 41, 59);
      doc.text(pageTitle, pageW / 2, 15, { align: 'center' });
    }
  }

  // PDF metadata
  doc.setProperties({
    title: title || 'WebGIS AI Agent',
    author: author || 'WebGIS AI Agent',
    subject: subtitle || '',
    creator: 'WebGIS AI Agent',
  });

  return doc.output('blob');
}

/**
 * audit #844: the never-wired vector-export pipeline (~340 lines:
 * generateMapSpecVectorSvgString / exportMapSpecToVectorSvg /
 * exportMapSpecToVectorPdf — zero production callers since #264/#668)
 * was removed as dead code. The UI's "SVG" option honestly wraps the
 * PNG bitmap (see buildSvgWrapper); a true vector path can be
 * reintroduced from git history when it gets a caller.
 */

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

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
  include_legend?: boolean;
  include_compass?: boolean;
  include_scale?: boolean;
  dark_mode?: boolean;
  /**
   * W9（ADR-0118）：多帧导出 —— 非空时走 frame-composer（atlas pages /
   * small-multiple grid）。布局裁决：format pdf → pages（多页）；
   * png/svg → grid（单画布拼板）。frameLayout 与 format 冲突时以 format
   * 为准（容器语义）。
   */
  frames?: ExportFrame[];
  frameLayout?: FrameLayout;
}

export interface ExportDeps {
  map: Map;
  getHudState: () => any;
  /** #527：高 DPI 分支的 idle 等待截止毫秒（测试注入用），默认 EXPORT_IDLE_TIMEOUT_MS。 */
  idleTimeoutMs?: number;
}

/** committed/live-composed MapSpec 的最小形状（layout 组件 + layers）。 */
interface ExportCommittedSpec {
  layout?: { components?: any[] };
  layers?: any[];
}

export interface ExportOutcome {
  ok: boolean;
  format: string;
  url?: string;
  filename?: string;
  error?: string;
}

interface LegendData {
  legendSpec: any | undefined;
  thematicLayer: any | undefined;
  heatmapLegend: {
    name?: string;
    paletteColors?: string[];
    /** ADR-0081 parity：热力图例的量化口径（min/max/unit）与 live
     *  FloatingLegend 同源（legend_spec），不再只画定性 低/高 标签。 */
    min?: number;
    max?: number;
    unit?: string;
  } | undefined;
}

export function discoverLegendData(layers: any[]): LegendData {
  // #679 修复延伸：热力层自带 legend_spec（连续色带）。此前 legendLayer 与
  // heatmapLegend 都命中同一热力层 → 导出成品画两个互相矛盾的色带图例
  //（legendSpec 版 + 硬编码 cyan→red 版）。规则：离散/分级图例优先取非
  // 热力层；热力层的色带交给 heatmapLegend，并携带 legend_spec.palette_colors
  // 使导出色带与 live 渲染同源（palette 漂移修复）。
  const nonHeatLegendLayer = layers.find(
    (l: any) => l.visible && l.legend_spec && l.type !== 'heatmap',
  );
  const heatmapLayer = layers.find(
    (l: any) => l.visible && l.type === 'heatmap',
  );
  const heatSpec = heatmapLayer?.legend_spec;
  const heatColors =
    heatSpec && (heatSpec.type === 'continuous' || heatSpec.type === 'divergent')
      ? heatSpec.palette_colors
      : undefined;
  const thematicLayerInfo = layers.find(
    (l: any) =>
      l.visible &&
      ((l.style as any)?.type === 'choropleth' ||
        (l.style as any)?.type === 'lisa' ||
        (l.source as any)?.metadata?.thematic_type === 'choropleth'),
  );

  return {
    legendSpec: nonHeatLegendLayer?.legend_spec,
    thematicLayer: (thematicLayerInfo?.style as any)?.type
      ? thematicLayerInfo?.style
      : thematicLayerInfo,
    heatmapLegend: heatmapLayer
      ? {
          name: heatmapLayer.name,
          paletteColors: heatColors,
          min: typeof heatSpec?.min === 'number' ? heatSpec.min : undefined,
          max: typeof heatSpec?.max === 'number' ? heatSpec.max : undefined,
          unit: typeof heatSpec?.unit === 'string' ? heatSpec.unit : undefined,
        }
      : undefined,
  };
}

export async function uploadExport(
  blob: Blob,
  filename: string,
  title?: string,
  degradations?: ExportDegradation[],
): Promise<{ url: string; filename: string }> {
  const form = new FormData();
  form.append('file', blob, filename);
  if (title) form.append('title', title);
  // V5（ADR-0118 D6）：诊断随成品上传 —— 服务端按权威词表校验后持久化
  // sidecar（POST /api/v1/export 的 render_diagnostics Form 字段），
  // 导出降级证据获得服务端锚点，不再只存在于一次对话系统消息里。
  if (degradations && degradations.length > 0) {
    form.append('render_diagnostics', JSON.stringify(degradations));
  }

  // 走统一 transport：rawBody 走 FormData，transport 不会 set Content-Type
  // (由浏览器自动加 multipart boundary)；typed ApiError 携带 FastAPI detail。
  return apiFetch<{ url: string; filename: string }>('/api/v1/export', {
    method: 'POST',
    rawBody: form,
    timeoutMs: 90_000, // PDF/PNG export can be large
    label: 'Export upload error',
  });
}

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

/** SVG 位图包装文本（W5：vector-svg-export 的 fallbackRaster 通道复用）。 */
function buildSvgText(
  canvas: HTMLCanvasElement,
  title: string,
  dataUrl: string,
): string {
  const w = canvas.width;
  const h = canvas.height;
  const safeTitle = (title || 'map').replace(/[<>&]/g, '');
  return (
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ` +
    `width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<title>${safeTitle}</title>` +
    `<image width="${w}" height="${h}" xlink:href="${dataUrl}"/>` +
    `</svg>`
  );
}

function buildSvgWrapper(
  canvas: HTMLCanvasElement,
  title: string,
  dataUrl: string,
): Blob {
  return new Blob([buildSvgText(canvas, title, dataUrl)], { type: 'image/svg+xml' });
}

/**
 * Wave 9 / W5：显式降级 → 导出后系统消息片段（有界披露：≤8 条，code+detail）。
 * 语义 = 「用户应知道导出件里少了/改了什么」，不静默。
 * review-r2：清单超 8 条时此前静默丢弃余量（如 50 帧 atlas 跳过 20 帧 →
 * 只列 8 条且无总数）—— 现在注明总数与截断，与「不静默」的自身语义一致。
 */
function formatDegradationNote(degradations: ExportDegradation[]): string {
  if (!degradations.length) return '';
  const listed = degradations.slice(0, 8);
  const omitted = degradations.length - listed.length;
  return (
    ` 注意：本次导出存在降级（共 ${degradations.length} 条` +
    (omitted > 0 ? `，此处仅列前 ${listed.length} 条` : '') +
    '）：' +
    listed
      .map((d) => `${d.code}${d.detail ? `（${d.detail}）` : ''}`)
      .join('、') +
    '。请如实告知用户。'
  );
}

/**
 * #527：高 DPI 分支在 `map.once('idle')` 上无界等待 —— WebGL 上下文丢失或画布
 * 隐藏时 idle 永不触发，finally 里的 pixelRatio 恢复永远不可达（3.125x @300DPI
 * → ~10x backing store 泄漏）。这里给等待加 deadline：超时抛类型化错误，走既有
 * catch（如实的失败文案）+ finally（恢复原始 pixelRatio）。exportCommands.ts 的
 * EXPORT_RENDER_TIMEOUT_MS 是队列级兜底（覆盖 render 不触发等路径），与内层
 * 截止互不替代。
 */
export const EXPORT_IDLE_TIMEOUT_MS = 30_000;

/** #527：idle 等待超时的类型化错误 —— catch 可识别并给出如实的失败文案。 */
export class MapIdleTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(
      `导出中止：地图在 ${timeoutMs}ms 内未进入 idle 状态` +
        `（可能 WebGL 上下文已丢失或画布被隐藏）`,
    );
    this.name = 'MapIdleTimeoutError';
  }
}

/** 有界等待 `map.once('idle')`：idle 触发即 resolve，截止前未触发即 reject。 */
export async function waitForMapIdle(map: Map, timeoutMs: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new MapIdleTimeoutError(timeoutMs)), timeoutMs);
    map.once('idle', () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/**
 * W7（ADR-0118）：导出侧 spec 事实源 = live 同一合成器（composeLiveMapSpec）
 * —— committed MapSpec 叠加 pendingPresentation/pendingRemoved。乐观可见性
 * 翻转/图层移除期间，导出与 live 读同一时刻的可见状态（此前导出只读
 * committed spec，两侧不同源）。hudState 形状与 map-panel reconcile effect
 * 的 HudToSpecInput 同一（layers/processLayers/activeFilters/selectionFilters/is3D）。
 */
export async function composeExportSpec(
  committed: unknown,
  hudState: {
    layers?: unknown[];
    processLayers?: Record<string, unknown>;
    activeFilters?: Record<string, unknown>;
    selectionFilters?: Record<string, unknown>;
    is3D?: boolean;
  },
  pending: Record<string, { visible?: boolean; opacity?: number }> = {},
  removed: string[] = [],
): Promise<unknown> {
  const { composeLiveMapSpec } = await import('@/lib/mapspec/live-spec');
  return composeLiveMapSpec(
    committed as Parameters<typeof composeLiveMapSpec>[0],
    {
      layers: (hudState.layers ?? []) as Parameters<typeof composeLiveMapSpec>[1]['layers'],
      processLayers: (hudState.processLayers ?? {}) as Parameters<typeof composeLiveMapSpec>[1]['processLayers'],
      activeFilters: (hudState.activeFilters ?? {}) as Parameters<typeof composeLiveMapSpec>[1]['activeFilters'],
      selectionFilters: (hudState.selectionFilters ?? {}) as Parameters<typeof composeLiveMapSpec>[1]['selectionFilters'],
      is3D: hudState.is3D ?? false,
    },
    pending,
    removed,
  );
}

/**
 * W8（ADR-0118）：swipe 对比导出组合 —— 副图 canvas 按 position 裁剪画在
 * 右侧（与 live clip-path inset(0 0 0 position*100%) 同侧同几何），并画
 * 分界线。主图/副图裁剪分数对齐（A4 裁剪下两图同一视口分数区域）。
 * @returns false = 副图 canvas 不可用（未渲染/跨域污染）—— 调用方回退
 *          主图单图导出并发 comparison_second_view_not_exported 诊断。
 */
export function composeComparisonOnExportCanvas(
  exportCanvas: HTMLCanvasElement,
  comparison: {
    getSecondCanvas: () => HTMLCanvasElement | null;
    kind: string;
    position: number;
  },
  crop: { srcX: number; srcY: number; srcW: number; srcH: number },
  base: { width: number; height: number },
): boolean {
  const second = comparison.getSecondCanvas();
  const ctx = exportCanvas.getContext('2d');
  if (!second || second.width === 0 || second.height === 0 || !ctx) return false;
  // 退化位置防御（与 clampSwipePosition 同方向的兜底，导出件至少 2% 可见）
  const pos = Math.min(0.98, Math.max(0.02, comparison.position || 0));
  // 主图裁剪分数（A4 居中裁剪）→ 同一分数应用到副图，保持地理对齐
  const fx = crop.srcX / base.width;
  const fy = crop.srcY / base.height;
  const fw = crop.srcW / base.width;
  const fh = crop.srcH / base.height;
  ctx.drawImage(
    second,
    second.width * (fx + fw * pos),
    second.height * fy,
    second.width * fw * (1 - pos),
    second.height * fh,
    exportCanvas.width * pos,
    0,
    exportCanvas.width * (1 - pos),
    exportCanvas.height,
  );
  // 分界线（白描边 + 深色芯 —— 亮暗主题都可辨）
  const dividerX = Math.round(exportCanvas.width * pos);
  ctx.save();
  ctx.fillStyle = 'rgba(255,255,255,0.9)';
  ctx.fillRect(dividerX - 1, 0, 2, exportCanvas.height);
  ctx.fillStyle = 'rgba(15,23,42,0.6)';
  ctx.fillRect(dividerX, 0, 1, exportCanvas.height);
  ctx.restore();
  return true;
}

/**
 * W9（ADR-0118）：多帧导出（atlas pages / small-multiple grid）。
 * 逐帧确定性执行（composeFrames）→ 容器裁决：format pdf → pages（多页，
 * 首页为封面嵌入第 1 帧）；png/svg → grid 单画布拼板（composeLayout 叠加
 * chrome —— 无地图相机输入，比例尺/罗盘自然缺席，不虚构比例）。
 * svg 无多帧矢量语义 → 位图包装 + vector_svg_fallback_raster（不静默）。
 */
async function runFrameExport(
  deps: ExportDeps,
  req: ExportRequest,
  frames: ExportFrame[],
  ctx: {
    title: string;
    subtitle?: string;
    author: string;
    dataSource: string;
    theme: 'light' | 'dark';
    paperSize: 'screen' | 'A4' | 'A3';
    orientation: 'landscape' | 'portrait';
    dpi: number;
    showWatermark: boolean;
    showMetadata: boolean;
  },
): Promise<ExportOutcome> {
  const { map, getHudState } = deps;
  const { composeFrames, composeGridCanvas } = await import('./frame-composer');
  const composed = await composeFrames(
    { map, waitForIdle: waitForMapIdle, idleTimeoutMs: deps.idleTimeoutMs },
    frames,
  );
  if (composed.canvases.length === 0) {
    // 全帧失败 → 如实失败（atlas_page_skipped 语义已在诊断中披露）
    throw new Error('多帧导出失败：所有帧均未完成（地图在预算内未就绪）');
  }
  const fmt = (req.format ?? 'png').toLowerCase();
  const degradationNote = formatDegradationNote(composed.degradations);
  // review-r2：成功消息必须注明跳过帧数（此前「N 帧成功」不提跳过 —— 跳过
  // 信息只藏在降级清单里，清单超 8 条时还会被截断）。
  const skippedFrames = composed.degradations.filter(
    (d) => d.code === 'atlas_page_skipped',
  ).length;
  const frameStat =
    skippedFrames > 0
      ? `成功 ${composed.canvases.length}/${composed.canvases.length + skippedFrames} 帧（${skippedFrames} 帧渲染失败已跳过）`
      : `${composed.canvases.length} 帧`;

  if (fmt === 'pdf') {
    const pdfDegradations: ExportDegradation[] = [];
    const pdfBlob = await MapExporterEngine.exportToPDF(
      composed.canvases[0],
      ctx.title,
      ctx.subtitle,
      {
        paperSize: (ctx.paperSize === 'A3' ? 'A3' : 'A4') as 'A4' | 'A3',
        orientation: ctx.orientation as 'landscape' | 'portrait',
        author: ctx.author,
        dataSource: ctx.dataSource,
        pages: composed.canvases.map((canvas, i) => ({ canvas, title: composed.titles[i] })),
        onDegradation: (d) => pdfDegradations.push(d),
      },
    );
    const upload = await uploadExport(
      pdfBlob, 'export-atlas.pdf', ctx.title,
      [...composed.degradations, ...pdfDegradations],
    );
    recordExport(getHudState, ctx.title, upload.filename, 'pdf', pdfBlob.size);
    getHudState().setPendingSystemMessage(
      `[系统通知] 图集 PDF \`${ctx.title || '未命名'}\` 已成功生成` +
        `（${frameStat}，首页为封面·嵌入第 1 帧；地图为位图画布 + 页标题矢量/栅格化混合文本层），` +
        `文件已落盘并分配URL：${upload.url}。请告知用户 PDF 已就绪，可通过以下链接下载：[下载PDF](${API_BASE}${upload.url})。` +
        degradationNote + formatDegradationNote(pdfDegradations) +
        `注意展示完链接后直接结束。`,
    );
    return { ok: true, format: 'pdf', url: upload.url, filename: upload.filename };
  }

  // grid 拼板（png / svg 容器）
  const grid = composeGridCanvas(composed.canvases, composed.titles);
  MapExporterEngine.composeLayout(grid, ctx.title, ctx.subtitle, {
    dpi: ctx.dpi,
    theme: ctx.theme,
    showScale: false,
    showCompass: false,
    showWatermark: ctx.showWatermark,
    showLegend: false,
    showMetadata: ctx.showMetadata,
    author: ctx.author,
    dataSource: ctx.dataSource,
  });
  const dataUrl = grid.toDataURL('image/png');

  if (fmt === 'svg') {
    const svgBlob = buildSvgWrapper(grid, ctx.title, dataUrl);
    const upload = await uploadExport(
      svgBlob, 'export-atlas.svg', ctx.title,
      [...composed.degradations,
       { code: 'vector_svg_fallback_raster', detail: '多帧拼板为位图合成' }],
    );
    recordExport(getHudState, ctx.title, upload.filename, 'svg', svgBlob.size);
    getHudState().setPendingSystemMessage(
      `[系统通知] 多帧拼板 SVG \`${ctx.title || '未命名'}\` 已成功生成` +
        `（${frameStat}，位图回退：多帧拼板为位图合成，不含矢量要素），` +
        `文件已落盘并分配URL：${upload.url}。可通过以下链接下载：[下载SVG](${API_BASE}${upload.url})。` +
        degradationNote +
        formatDegradationNote([{ code: 'vector_svg_fallback_raster', detail: '多帧拼板为位图合成' }]) +
        `注意展示完链接后直接结束。`,
    );
    return { ok: true, format: 'svg', url: upload.url, filename: upload.filename };
  }

  const res = await fetch(dataUrl);
  const blob = await res.blob();
  const upload = await uploadExport(blob, 'export-atlas.png', ctx.title, composed.degradations);
  recordExport(getHudState, ctx.title, upload.filename, 'png', blob.size);
  getHudState().setPendingSystemMessage(
    `[系统通知] 多帧拼板图 \`${ctx.title || '未命名'}\` 已成功生成` +
      `（${frameStat} grid 拼板，行优先 + 每帧小标题），` +
      `文件已落盘并分配URL：${upload.url}。 请利用Markdown的图片语法 \`![地图](${API_BASE}${upload.url})\` 将该成品展示给用户。` +
      degradationNote + `注意展示完图片后直接结束。`,
  );
  return { ok: true, format: 'png', url: upload.url, filename: upload.filename };
}

export async function runExport(
  deps: ExportDeps,
  req: ExportRequest,
): Promise<ExportOutcome> {
  const { map, getHudState } = deps;
  // #667 export-vector: vector exports (svg) need real features; hydrate MVT layers on demand
  const fmtEarly = String((req as any)?.format ?? 'png').toLowerCase();
  if (fmtEarly === 'svg') {
    try {
      await hydrateMvtLayers(getHudState().layers as any[], 'export-vector');
    } catch { /* best-effort */ }
  }
  const {
    title = '',
    subtitle,
    author = '',
    dataSource = '',
    showWatermark = true,
    showLegend = (req.showLegend ?? req.include_legend ?? true) as boolean,
    showMetadata = true,
    showGraticules = false,
    dark_mode,
    format = 'png',
  } = req || {};

  // #805: export_layout 组件是版面参数的 spec 层 —— A4/300dpi 的报告意图
  // 提交进 MapSpec 后，未显式传参的导出请求应采用它（此前组件是死配置，
  // 一律落到 screen/96 默认）。
  let layoutOpts: Record<string, unknown> = {};
  try {
    const { getCommittedMapSpec } = await import('@/lib/mapspec/session-cursor');
    const layoutComps = getCommittedMapSpec()?.layout?.components ?? [];
    const exportLayoutComp = layoutComps.find(
      (c) => c.type === 'export_layout' && c.enabled !== false,
    );
    layoutOpts = (exportLayoutComp?.options ?? {}) as Record<string, unknown>;
  } catch {
    /* spec 面缺席 → 内置默认 */
  }
  const paperSize = (req.paperSize ??
    (typeof layoutOpts['paperSize'] === 'string' ? layoutOpts['paperSize'] : undefined) ??
    'screen') as 'screen' | 'A4' | 'A3';
  const orientation = (req.orientation ??
    (typeof layoutOpts['orientation'] === 'string' ? layoutOpts['orientation'] : undefined) ??
    'landscape') as 'landscape' | 'portrait';
  const dpi = (req.dpi ??
    (typeof layoutOpts['dpi'] === 'number' && layoutOpts['dpi'] > 0 ? layoutOpts['dpi'] : undefined) ??
    96) as number;

  try {
    getHudState().setPendingSystemMessage(
      `[系统通知] 正在生成 ${String(format).toUpperCase()} 导出文件…`,
    );
  } catch {
    /* defensive */
  }

  const hudTheme = getHudState().theme;
  // #614：dark_mode 请求参数必须优先于 HUD 主题 —— 浅色 HUD 下默认参数
  // (dark_mode=True) 也应产出暗色成品，与后端回告 Agent 的口径一致。
  const theme: 'light' | 'dark' =
    (dark_mode ?? hudTheme === 'dark') ? 'dark' : 'light';
  const origPixelRatio = map.getPixelRatio();
  const targetPixelRatio = dpi / 96;
  try {
    if (targetPixelRatio > 1) {
      map.setPixelRatio(targetPixelRatio);
      // #527: 有界 idle 等待 —— 超时抛 MapIdleTimeoutError，让下方 catch
      // 给出如实的失败文案、finally 恢复原始 pixelRatio（此前无界等待在
      // WebGL 上下文丢失时挂死并泄漏 pixelRatio）。
      await waitForMapIdle(map, deps.idleTimeoutMs ?? EXPORT_IDLE_TIMEOUT_MS);
    }

    const baseCanvas = map.getCanvas();
    // #802: 默认 dpi=96 路径不调用 setPixelRatio，导出画布就是浏览器原生
    // backing store（css·devicePixelRatio）—— 比例尺/经纬网换算需要真实的
    // 设备像素比，而非 dpi/96（HiDPI 上此前恰好错 dpr 倍）。clientWidth
    // 不可得（测试环境）时回退到有效 pixelRatio。
    const canvasDpr =
      baseCanvas.clientWidth > 0
        ? baseCanvas.width / baseCanvas.clientWidth
        : targetPixelRatio > 1
          ? targetPixelRatio
          : 1;
    const prepare = prepareExportCanvas(baseCanvas, {
      paperSize: paperSize as any,
      orientation: orientation as any,
      dpi: 96,
    });
    const exportCanvas = prepare.canvas;

    // W8（ADR-0118）：swipe 对比导出组合 —— 副图视图按 position 裁剪进
    // 导出件 + 分界线（此前对比态导出静默只截主图）。chrome 绘制在前，
    // 整饰（标题/图例）不受副图覆盖影响。SVG 真矢量件的数据层来自 MapSpec
    // 全幅编译（不含副图位图视图）→ 如实披露副图未进导出件，不假装组合。
    const comparisonDegradations: ExportDegradation[] = [];
    const comparison = getComparisonExport();
    if (comparison) {
      if (fmtEarly === 'svg') {
        comparisonDegradations.push({
          code: 'comparison_second_view_not_exported',
          detail: '矢量 SVG 不含对比副图视图（数据层来自 MapSpec）',
        });
      } else {
        const composed = composeComparisonOnExportCanvas(
          exportCanvas,
          comparison,
          { srcX: prepare.srcX, srcY: prepare.srcY, srcW: prepare.srcW, srcH: prepare.srcH },
          { width: baseCanvas.width, height: baseCanvas.height },
        );
        comparisonDegradations.push(
          composed
            ? { code: 'comparison_export_composed', detail: `${Math.round(comparison.position * 100)}%` }
            : { code: 'comparison_second_view_not_exported', detail: '副图画布未渲染或不可读，仅导出主图视图' },
        );
      }
    }

    const storeState = getHudState();
    const { legendSpec, thematicLayer, heatmapLegend } = discoverLegendData(
      storeState.layers,
    );

    // GIS Harness 组件面：live chrome 与 export 共用 MapSpec layout.components
    // （§21 单一 desired state）。语义：组件类型存在 → 其 enabled 值生效；
    // 类型不存在 → 保持内置默认 true（与 live 端 hasSpecChrome 排除集一致，
    // 只有 export_layout 等非可视组件的 spec 不会误关罗盘/比例尺）。
    // 显式请求参数 > spec 组件 > 内置默认 —— 旧 spec（无 components）行为
    // 完全不变。
    let specTitle = '';
    let specSubtitle = '';
    let specShowCompass: boolean | undefined;
    let specShowScale: boolean | undefined;
    let committedSpec: ExportCommittedSpec | null = null;
    try {
      // W7：导出 spec 经 live 同一合成器（composeLiveMapSpec）—— 叠加
      // pendingPresentation/pendingRemoved，与 live 同一时刻的可见状态。
      const {
        getCommittedMapSpec,
        getPendingPresentation,
        getPendingRemoved,
      } = await import('@/lib/mapspec/session-cursor');
      committedSpec = (await composeExportSpec(
        getCommittedMapSpec() ?? null,
        storeState,
        getPendingPresentation(),
        getPendingRemoved(),
      )) as ExportCommittedSpec;
      const specComps = committedSpec?.layout?.components ?? [];
      if (specComps.length) {
        const isEnabled = (t: string) =>
          specComps.some((c) => c.type === t && c.enabled !== false);
        const hasType = (t: string) => specComps.some((c) => c.type === t);
        const titleComp = specComps.find((c) => c.type === 'title' && c.enabled !== false);
        if (titleComp && typeof titleComp.options?.['text'] === 'string') {
          specTitle = titleComp.options['text'];
        }
        // v3(Phase H)：subtitle 与 title 同一事实源链 —— 请求参数 > spec
        // 组件 > 内置空串。此前 subtitle 只读请求参数，spec 里改了副标题
        // 的导出成品不跟随（title/subtitle 行为分叉）。
        const subtitleComp = specComps.find((c) => c.type === 'subtitle' && c.enabled !== false);
        if (subtitleComp && typeof subtitleComp.options?.['text'] === 'string') {
          specSubtitle = subtitleComp.options['text'];
        }
        if (hasType('north_arrow')) specShowCompass = isEnabled('north_arrow');
        if (hasType('scale_bar')) specShowScale = isEnabled('scale_bar');
      }
    } catch {
      /* spec 面缺席 → 走请求/内置默认 */
    }

    // W6（ADR-0118）：PDF 单一标题事实源 —— 标题/副标题全部 WinAnsi 可编码
    // → 画布不画（skipTitle），doc.text 矢量书写一次；含非 WinAnsi（CJK 等）
    // → 反向：画布栅格化承载，PDF 文本层跳过 + pdf_text_rasterized_cjk 诊断
    //（jsPDF 标准字体写不了 CJK，此前双标题 + 中文乱码并存）。
    const effTitle = title || specTitle || '';
    const effSubtitle = subtitle || specSubtitle || '';
    const pdfVectorText =
      !hasNonWinAnsiChars(effTitle) && !hasNonWinAnsiChars(effSubtitle);
    const pdfSkipCanvasTitle = fmtEarly === 'pdf' && pdfVectorText;

    // W9（ADR-0118）：多帧导出分支 —— frames 非空时走 frame-composer
    // （逐帧 filter/viewport 变换 + 有界 idle + 抓帧 + 恢复）；pixelRatio
    // 增益由上方统一设置、finally 统一恢复，帧抓取同享 dpi 增益。
    if (Array.isArray(req.frames) && req.frames.length > 0) {
      return await runFrameExport(deps, req, req.frames, {
        title: effTitle,
        subtitle: effSubtitle,
        author,
        dataSource,
        theme,
        paperSize,
        orientation,
        dpi,
        showWatermark,
        showMetadata,
      });
    }

    // ADR-0081 Export Parity：spec 组件在场时构建 chrome 模型 —— placement
    // （anchor 七槽 + floating 像素坐标）、图例/色条 enabled、统计卡/图表
    // 面板全部从 MapSpec 组件出发（与 live 共用 resolveMapComponents）。
    // 无 spec 组件 → fromSpec=false，composeLayout 走 legacy 固定槽。
    let chromeModel: ExportChromeModel | undefined;
    try {
      const legendSpecsByLayer: Record<string, any> = {};
      for (const l of (storeState.layers as any[]) || []) {
        if (l?.id && l.legend_spec) legendSpecsByLayer[l.id] = l.legend_spec;
      }
      for (const l of committedSpec?.layers ?? []) {
        if (l?.id && (l as any).legend_spec) legendSpecsByLayer[l.id] = (l as any).legend_spec;
      }
      const hasColorbarComp = (committedSpec?.layout?.components ?? []).some(
        (c) => c.type === 'continuous_colorbar',
      );
      // 热力层无 colorbar 组件时，live 仍渲染 FloatingLegend（量化 min/max/unit）
      // —— 导出同源合成一条连续色条，不再退化为定性 低/高 标签。
      const fallbackColorbarSpec =
        !hasColorbarComp && heatmapLegend?.paletteColors
          ? {
              type: 'continuous',
              field: heatmapLegend.name,
              min: heatmapLegend.min,
              max: heatmapLegend.max,
              palette_colors: heatmapLegend.paletteColors,
              unit: heatmapLegend.unit,
            }
          : undefined;
      chromeModel = await buildExportChrome(
        {
          spec: committedSpec,
          viewport: {
            width: baseCanvas.clientWidth || 0,
            height: baseCanvas.clientHeight || 0,
          },
          requestTitle: title || undefined,
          requestSubtitle: subtitle || undefined,
          legendSpecsByLayer,
          fallbackLegendSpec: legendSpec,
          // v2：live 视口地理 bounds —— inset 指示框缺省 mainBbox 时的
          // 自动确定来源（Scenario C：学术图 + 区位插图导出）
          viewportBounds: (() => {
            try {
              const b = map.getBounds();
              return {
                west: b.getWest(), south: b.getSouth(),
                east: b.getEast(), north: b.getNorth(),
              };
            } catch {
              return undefined;
            }
          })(),
          loadChart: async (ref) => {
            const { loadChartArtifact } = await import(
              '@/lib/map-components/chart-artifact'
            );
            return (await loadChartArtifact(ref)) as any;
          },
          // V4：tableRef 通道 —— 与 chart 同一 artifact 装配模式（原始载荷
          // 交回 export-chrome 统一做有界快照；拉取失败 → 面板缺席，不画空表）。
          loadTable: async (ref) => {
            const { loadTableArtifact } = await import(
              '@/lib/map-components/table-data'
            );
            return await loadTableArtifact(ref);
          },
          // V4：layerId 通道 —— HUD 图层属性行（live table-panel 同源读取
          // layer.source.features.properties；层缺席/无要素 → 面板缺席）。
          loadLayerTable: async (layerId) => {
            const { useHudStore } = await import('@/lib/store/useHudStore');
            const layers = useHudStore.getState().layers as Array<{
              id: string;
              name?: string;
              _mapspecLayerId?: string;
              source?: { features?: Array<{ properties?: Record<string, unknown> }> };
            }>;
            const layer = layers.find(
              (l) => l.id === layerId || l._mapspecLayerId === layerId,
            );
            const features = layer?.source?.features;
            if (!Array.isArray(features) || features.length === 0) return null;
            return features.map((f) => f.properties ?? {});
          },
        },
        { width: exportCanvas.width, height: exportCanvas.height },
      );
      if (fallbackColorbarSpec && typeof fallbackColorbarSpec.min === 'number') {
        chromeModel.colorbar = {
          kind: 'colorbar',
          anchor: 'bottom-right',
          legendSpec: fallbackColorbarSpec as any,
        };
      }
    } catch (e) {
      devOnly.warn('[MapExporter] chrome model build failed — legacy layout', e);
      chromeModel = undefined;
    }

    // #614：经 MapExporterEngine 调 composeLayout（与 exportToPDF 同款路由），
    // 便于测试 spyOn 断言 theme 选项（模块内直接绑定无法被 mock 拦截）。
    MapExporterEngine.composeLayout(exportCanvas, effTitle, effSubtitle, {
      dpi,
      theme,
      // #802: 按真实画布设备像素比换算（dpi 参数仍驱动布局字号/边距缩放）
      pixelsPerLogicalPx: canvasDpr,
      // W6：PDF 矢量文本层时画布不画标题（单一事实源 —— PDF 头部 doc.text）
      skipTitle: pdfSkipCanvasTitle,
      showScale: req.showScale ?? req.include_scale ?? specShowScale ?? true,
      showCompass: req.showCompass ?? req.include_compass ?? specShowCompass ?? true,
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
      chrome: chromeModel,
    });

    const dataUrl = exportCanvas.toDataURL('image/png');
    const fmt = fmtEarly;

    // Wave 9：显式降级汇入导出后系统消息（此前 chart/table 面板拉取失败
    // 静默缺席 —— 用户不知道导出件里少了东西）。W8：对比组合/回退诊断并入。
    const chromeDegradations = [
      ...(chromeModel?.degradations ?? []),
      ...comparisonDegradations,
    ];

    if (fmt === 'svg') {
      // V5（ADR-0118 W5）：真矢量优先 —— 孪生编译器产出数据层矢量要素 +
      // svg-marginalia 整饰（图框/指北针/比例尺/图例）；编译/合成异常回退
      // 既有位图包装（<image> 嵌 PNG）并显式发 vector_svg_fallback_raster。
      let svgText: string;
      let svgDegradations: ExportDegradation[];
      try {
        const { buildVectorSvgExport } = await import('./vector-svg-export');
        const vector = buildVectorSvgExport({
          spec: committedSpec,
          viewport: { width: exportCanvas.width, height: exportCanvas.height },
          paperSize,
          orientation,
          dpi,
          title: title || specTitle || '',
          subtitle: subtitle || specSubtitle || '',
          chromeModel,
          metersPerPixel: (() => {
            try {
              return metersPerPixelAt(map.getZoom(), map.getCenter().lat);
            } catch {
              return undefined;
            }
          })(),
          fallbackRaster: () => buildSvgText(exportCanvas, title, dataUrl),
        });
        svgText = vector.svg;
        svgDegradations = vector.degradations;
      } catch (e) {
        // fallbackRaster 未注入/自身抛错的双保险位图回退（不静默）。
        devOnly.warn('[MapExporter] vector svg build failed — raster fallback', e);
        const wrapper = buildSvgWrapper(exportCanvas, title, dataUrl);
        svgText = await wrapper.text();
        svgDegradations = [
          { code: 'vector_svg_fallback_raster', detail: e instanceof Error ? e.message : String(e) },
        ];
      }
      const rasterFallback = svgDegradations.some((d) => d.code === 'vector_svg_fallback_raster');
      const svgBlob = new Blob([svgText], { type: 'image/svg+xml' });
      const upload = await uploadExport(
        svgBlob, 'export.svg', title, [...chromeDegradations, ...svgDegradations],
      );
      recordExport(getHudState, title, upload.filename, 'svg', svgBlob.size);
      getHudState().setPendingSystemMessage(
        `[系统通知] 专题地图 SVG \`${title || '未命名'}\` 已成功生成` +
          `（${rasterFallback ? '位图回退：数据层矢量编译失败，含嵌入位图' : '真矢量：数据层矢量要素，不含栅格底图'}），` +
          `文件已落盘并分配URL：${upload.url}。可通过以下链接下载：[下载SVG](${API_BASE}${upload.url})。` +
          formatDegradationNote([...chromeDegradations, ...svgDegradations]) +
          `注意展示完链接后直接结束。`,
      );
      return { ok: true, format: 'svg', url: upload.url, filename: upload.filename };
    } else if (fmt === 'pdf') {
      // W6（ADR-0118）：文本层状态判定 —— CJK 等非 WinAnsi 字符已在画布
      // 栅格化承载（composeLayout 正常画），PDF 文本层跳过 title/subtitle；
      // ASCII 文本走 doc.text 真矢量。地图本体恒为位图画布嵌入（如实披露）。
      const pdfDegradations: ExportDegradation[] = pdfVectorText
        ? []
        : [
            {
              code: 'pdf_text_rasterized_cjk',
              detail: '标题/副标题含非 WinAnsi 字符，已随画布栅格化（PDF 文本层跳过，避免乱码）',
            },
          ];
      // ADR-0081：PDF 文本层 subtitle 与 canvas 同一事实源链（请求参数 >
      // spec 组件 > 空串）—— 此前 PDF 只读请求参数，spec 副标题在 PDF
      // 文本层静默丢失。
      const pdfBlob = await MapExporterEngine.exportToPDF(
        exportCanvas,
        effTitle,
        effSubtitle,
        {
          paperSize: (paperSize === 'A3' ? 'A3' : 'A4') as 'A4' | 'A3',
          orientation: orientation as 'landscape' | 'portrait',
          author,
          dataSource,
          textLayer: pdfVectorText ? 'vector' : 'skip',
        },
      );
      const upload = await uploadExport(
        pdfBlob, 'export.pdf', title, [...chromeDegradations, ...pdfDegradations],
      );
      recordExport(getHudState, title, upload.filename, 'pdf', pdfBlob.size);
      getHudState().setPendingSystemMessage(
        `[系统通知] 专题底图 PDF \`${title || '未命名'}\` 已成功生成` +
          `（地图为位图画布 + 文本层${pdfVectorText ? '矢量（标题/副标题为 PDF 矢量文本）' : '栅格化（标题/副标题随画布位图，避免 CJK 乱码）'}），` +
          `文件已落盘并分配URL：${upload.url}。` +
          `请告知用户 PDF 已就绪，可通过以下链接下载：[下载PDF](${API_BASE}${upload.url})。` +
          formatDegradationNote([...chromeDegradations, ...pdfDegradations]) +
          `注意展示完链接后直接结束。`,
      );
      return { ok: true, format: 'pdf', url: upload.url, filename: upload.filename };
    } else {
      const res = await fetch(dataUrl);
      const blob = await res.blob();
      const upload = await uploadExport(blob, 'export.png', title, chromeDegradations);
      recordExport(getHudState, title, upload.filename, 'png', blob.size);
      getHudState().setPendingSystemMessage(
        `[系统通知] 专题地图 \`${title || '未命名'}\` 已成功排版合成，` +
          `文件已落盘并分配URL：${upload.url}。 请利用Markdown的图片语法 \`![地图](${API_BASE}${upload.url})\` 将该成品展示给用户，并祝其研究顺利！` +
          formatDegradationNote(chromeDegradations) + `注意展示完图片后直接结束。`,
      );
      return { ok: true, format: 'png', url: upload.url, filename: upload.filename };
    }
  } catch (e) {
    devOnly.error('[MapExporter] Canvas extraction/export failed', e);
    // #527：idle 超时是类型化错误 —— 给出如实的失败原因（而不是把超时淹在
    // 泛化的"排版合成失败"里），并说明像素比已恢复。
    const idleTimeout = e instanceof MapIdleTimeoutError;
    // #469：上传接口需要认证 —— 会话过期/token 失效时给出明确的登录指引，
    // 而不是把 401 淹没在通用失败文案里（匿名路径已由导出按钮门控）。
    const authRequired = isApiError(e) && (e.status === 401 || e.status === 403);
    const errorMsg = idleTimeout
      ? e.message
      : authRequired
        ? '导出需要登录（认证失败或会话已过期）'
        : e instanceof Error
          ? e.message
          : String(e);
    getHudState().setPendingSystemMessage(
      idleTimeout
        ? `[系统通知] ${e.message}。已恢复原始分辨率。请告知用户并结束流程。`
        : authRequired
          ? '[系统通知] 导出失败：导出功能需要登录账号（认证失败或会话已过期）。请到 设置 → 账户 重新登录后再导出。'
          : `[系统通知] 专题地图排版合成失败。错误原因: ${e}。请向用户致歉并结束流程。`,
    );
    return { ok: false, format: (format ?? 'png').toLowerCase(), error: errorMsg };
  } finally {
    if (targetPixelRatio > 1) {
      map.setPixelRatio(origPixelRatio);
    }
  }
}

/**
 * Deep MapExporterEngine consolidating canvas capture, layout composition,
 * format branching, SVG marginalia, and vector PDF rendering.
 */
export class MapExporterEngine {
  static export = runExport;
  static exportToPDF = exportToPDF;
  static prepareExportCanvas = prepareExportCanvas;
  static composeLayout = composeLayout;
  static downloadBlob = downloadBlob;
}
