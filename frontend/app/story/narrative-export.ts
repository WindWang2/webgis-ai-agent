'use client';

import { captureMapCanvas } from '@/lib/map-kit/exporter';
import type { Map } from 'maplibre-gl';

/**
 * 分享卡 / 叙事 PDF 产物合成（ADR-0147）。
 *
 * #1213 评估结论（写入 ADR-0147，PR 协调点重申）：`/api/v1/export/vector-pdf`
 * 是单 MapSpec 出版引擎（WeasyPrint，帧来自 spec），无 chart/table 章节位，
 * 且 ref 载体源须调用方内联 —— 不适合异构叙事文档。故叙事 PDF 走既有栅格
 * 多页链（exporter.exportToPDF 的 pages 参数），分享卡用 captureMapCanvas
 * + canvas 合成，全部为既有 exporter 能力，零新增后端面。
 */

const CARD_W = 1200;
const CARD_H = 630;

function loadImage(blob: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('地图快照解码失败'));
    };
    img.src = url;
  });
}

/**
 * OG 分享卡：地图快照 cover 裁切 + 底部渐变遮罩 + 章节标题 + 品牌条。
 * 纯本地 canvas 合成，无网络。
 */
export async function composeShareCard(
  map: Map,
  meta: { title: string; subtitle?: string; brand?: string },
): Promise<Blob> {
  const snapBlob = await captureMapCanvas(map);
  const img = await loadImage(snapBlob);

  const canvas = document.createElement('canvas');
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D 不可用');

  // cover 裁切
  const scale = Math.max(CARD_W / img.width, CARD_H / img.height);
  const dw = img.width * scale;
  const dh = img.height * scale;
  ctx.fillStyle = '#0b1220';
  ctx.fillRect(0, 0, CARD_W, CARD_H);
  ctx.drawImage(img, (CARD_W - dw) / 2, (CARD_H - dh) / 2, dw, dh);

  // 底部渐变遮罩
  const grad = ctx.createLinearGradient(0, CARD_H * 0.45, 0, CARD_H);
  grad.addColorStop(0, 'rgba(11,18,32,0)');
  grad.addColorStop(1, 'rgba(11,18,32,0.92)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, CARD_H * 0.45, CARD_W, CARD_H * 0.55);

  // 标题（自动换行，最多 2 行）
  ctx.fillStyle = '#f8fafc';
  ctx.font = '600 44px system-ui, sans-serif';
  ctx.textBaseline = 'alphabetic';
  const title = meta.title.length > 28 ? `${meta.title.slice(0, 27)}…` : meta.title;
  ctx.fillText(title, 48, CARD_H - (meta.subtitle ? 108 : 64));

  if (meta.subtitle) {
    ctx.fillStyle = 'rgba(248,250,252,0.75)';
    ctx.font = '24px system-ui, sans-serif';
    const sub = meta.subtitle.length > 60 ? `${meta.subtitle.slice(0, 59)}…` : meta.subtitle;
    ctx.fillText(sub, 48, CARD_H - 64);
  }

  // 品牌条
  ctx.fillStyle = 'rgba(248,250,252,0.55)';
  ctx.font = '500 20px system-ui, sans-serif';
  ctx.fillText(meta.brand ?? 'GeoAgent · StoryMap', CARD_W - 260, 48);

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('分享卡导出失败'))), 'image/png');
  });
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** blob → HTMLCanvasElement（exportToPDF pages 参数需要 canvas）。 */
export async function blobToCanvas(blob: Blob): Promise<HTMLCanvasElement> {
  const img = await loadImage(blob);
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D 不可用');
  ctx.drawImage(img, 0, 0);
  return canvas;
}

export interface NarrativePdfChapterInput {
  id: string;
  title: string;
}

export interface NarrativePdfProgress {
  current: number;
  total: number;
  chapterTitle: string;
}

/**
 * 叙事 PDF 导出：逐章定位 → 等待相机/图层渲染 → 快照 → exportToPDF 多页。
 * capture 由调用方注入（页面持有 map 实例与激活回调），本模块只管编排，
 * 便于单测。
 */
export async function exportNarrativePdf(
  chapters: NarrativePdfChapterInput[],
  capture: (chapter: NarrativePdfChapterInput) => Promise<Blob>,
  docTitle: string,
  onProgress?: (p: NarrativePdfProgress) => void,
): Promise<Blob> {
  if (chapters.length === 0) throw new Error('没有可导出的章节（全部隐藏或会话为空）');
  const { exportToPDF } = await import('@/lib/map-kit/exporter');
  // exportToPDF 的 pages 契约（W9）：pages[0].canvas 即封面页 —— 全部章节
  // 都要走 pages（含第 1 章），主 canvas 参数在 pages 在场时被忽略。
  const pages: Array<{ canvas: HTMLCanvasElement; title?: string }> = [];
  for (let i = 0; i < chapters.length; i++) {
    const chapter = chapters[i];
    onProgress?.({ current: i + 1, total: chapters.length, chapterTitle: chapter.title });
    const blob = await capture(chapter);
    pages.push({ canvas: await blobToCanvas(blob), title: chapter.title });
  }
  return exportToPDF(pages[0].canvas, docTitle, `${chapters.length} 个章节`, {
    paperSize: 'A4',
    orientation: 'landscape',
    pages,
  });
}
