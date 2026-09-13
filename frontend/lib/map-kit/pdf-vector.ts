/**
 * PDF Vector Body —— PDF 图体矢量化（V11 W6.2，ADR-0166，缺口 G9）。
 *
 * V10 现状：PDF 图体 `canvas.toDataURL('image/png')` → `addImage` 栅格
 * （exporter.ts:1007/1022）。W6 改为：**矢量 SVG 体经 svg2pdf.js 嵌入
 * PDF 路径操作符**（文字层保持既有 CJK 字体嵌入路径不变）；矢量不可用
 * （无 SVG 源 / 转换异常）→ 诚实回退栅格并披露（不伪矢量）。
 *
 * 依赖：svg2pdf.js（MIT，与 jsPDF 官方配对的 SVG→PDF 路径转换器）；
 * 需要 DOM（浏览器/jsdom）解析 SVG —— 动态 import 保持导出器轻载。
 */
import type { jsPDF } from 'jspdf';

export type PdfBodyMode = 'vector' | 'raster';

/**
 * 把 SVG 字符串作为**矢量**嵌入 jsPDF 文档；成功 true，失败 false（调用方
 * 回退栅格）。异常全捕（不抛 —— 导出主链路 fail-soft）。
 */
export async function addVectorSvgBody(
  doc: jsPDF,
  svgString: string,
  x: number,
  y: number,
  width: number,
  height: number,
): Promise<boolean> {
  if (!svgString || !svgString.includes('<svg')) return false;
  try {
    // 包 main 是 UMD（依赖全局 jsPDF 的 peer 模式，Vite/ESM 互操作下
    // import 即炸）；显式走 **ESM 构建**（package.json module 字段同款），
    // 并对齐其 peer：导入前把构造器挂到全局（fonts.ts 初始化读 jsPDF.API）。
    const jspdfMod = await import('jspdf');
    const JsPDFCtor = (jspdfMod as { jsPDF?: unknown }).jsPDF
      ?? (jspdfMod as { default: unknown }).default;
    (globalThis as Record<string, unknown>).jsPDF = JsPDFCtor;
    const mod = await import('svg2pdf.js/dist/svg2pdf.es.js');
    const svgToPdf = (mod as { svg?: unknown }).svg
      ?? (mod as { default?: unknown; svg2pdf?: unknown }).svg2pdf
      ?? (mod as { default: unknown }).default;
    if (typeof svgToPdf !== 'function') return false;
    const parsed = new DOMParser().parseFromString(svgString, 'image/svg+xml');
    const root = parsed.documentElement as unknown as SVGElement;
    if (!root || root.nodeName.toLowerCase() !== 'svg') return false;
    await (svgToPdf as (el: Element, d: jsPDF, o: object) => Promise<void>)(
      root, doc, { x, y, width, height });
    return true;
  } catch {
    return false;
  }
}

/**
 * PDF 体模式探测（测试/验收）：arraybuffer 里是否含图像 XObject。
 * 矢量体 → 无 `/Subtype /Image`（路径操作符绘制）；栅格体 → 有。
 */
export function pdfHasRasterImage(pdfBytes: ArrayBuffer): boolean {
  const text = new TextDecoder('latin1').decode(new Uint8Array(pdfBytes));
  return /\/Subtype\s*\/Image/.test(text);
}
