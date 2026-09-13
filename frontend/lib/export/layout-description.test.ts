import { describe, it, expect } from 'vitest';
import {
  buildPublicationLayout,
  frameAspectWH,
  PUBLICATION_LAYOUT_VERSION,
  PUBLICATION_BLEED_MM,
} from './layout-description';
import { computeNiceScale, formatScaleLabel } from '../map-kit/scale-math';
import type { ExportChromeModel } from '../map-kit/export-chrome';

function chromeModel(partial: Partial<ExportChromeModel> = {}): ExportChromeModel {
  return {
    fromSpec: true,
    degradations: [],
    colorbars: [],
    insets: [],
    panels: [],
    legends: [],
    ...partial,
  } as ExportChromeModel;
}

const BASE = {
  paperSize: 'A4' as const,
  orientation: 'landscape' as const,
  dpi: 300,
  frame: { width: 2480, height: 1754 },
  chromeModel: null,
};

describe('buildPublicationLayout（ADR-0157 P2 版面描述中间层）', () => {
  it('version pin + 确定性：同输入两次装配 JSON 全等', () => {
    expect(PUBLICATION_LAYOUT_VERSION).toBe(1);
    const a = buildPublicationLayout({ ...BASE, requestTitle: '成图' });
    const b = buildPublicationLayout({ ...BASE, requestTitle: '成图' });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it('标题回退链：请求参数 > spec 组件 > 空串（三链单一记录）', () => {
    const l1 = buildPublicationLayout({ ...BASE, requestTitle: 'Req', specTitle: 'Spec' });
    expect(l1.texts.find((t) => t.kind === 'title')?.text).toBe('Req');
    const l2 = buildPublicationLayout({ ...BASE, specTitle: 'Spec' });
    expect(l2.texts.find((t) => t.kind === 'title')?.text).toBe('Spec');
    const l3 = buildPublicationLayout(BASE);
    expect(l3.texts.find((t) => t.kind === 'title')).toBeUndefined();
  });

  it('比例尺：scale-math 单源数字（画布/SVG 同记录）；无依据 → null 不虚构', () => {
    const l1 = buildPublicationLayout({ ...BASE, metersPerPixel: 100 });
    expect(l1.scaleBar).not.toBeNull();
    const nice = computeNiceScale(100, 120);
    expect(l1.scaleBar!.nice).toEqual(nice);
    expect(l1.scaleBar!.label).toBe(formatScaleLabel(nice.meters));
    expect(l1.scaleBar!.label).toContain('km');
    const l2 = buildPublicationLayout(BASE);
    expect(l2.scaleBar).toBeNull();
  });

  it('出版档（cmyk）：bleed=3mm + cropMarks；srgb 档两者关闭', () => {
    const print = buildPublicationLayout({ ...BASE, colorMode: 'cmyk' });
    expect(print.page.colorMode).toBe('cmyk');
    expect(print.page.bleedMm).toBe(PUBLICATION_BLEED_MM);
    expect(print.page.bleedMm).toBe(3);
    expect(print.page.cropMarks).toBe(true);
    const srgb = buildPublicationLayout(BASE);
    expect(srgb.page.bleedMm).toBe(0);
    expect(srgb.page.cropMarks).toBe(false);
  });

  it('数据超界 → extent_overflow_data 降级并入装配记录；未超界不发', () => {
    const over = buildPublicationLayout({
      ...BASE,
      extent: { mask: [104, 30, 104.1, 30.1], export: [104, 30, 104.1, 30.1], dataOverflow: true },
    });
    expect(over.degradations.map((d) => d.code)).toContain('extent_overflow_data');
    const inside = buildPublicationLayout({
      ...BASE,
      extent: { mask: [104, 30, 104.1, 30.1], export: [104, 30, 104.1, 30.1], dataOverflow: false },
    });
    expect(inside.degradations.map((d) => d.code)).not.toContain('extent_overflow_data');
  });

  it('chrome 模型降级透传进装配记录（canvas/SVG 同一降级面）', () => {
    const cm = chromeModel({
      degradations: [{ code: 'legend_entries_truncated', detail: '5→3' }],
    });
    const l = buildPublicationLayout({ ...BASE, chromeModel: cm });
    expect(l.degradations).toContainEqual({ code: 'legend_entries_truncated', detail: '5→3' });
    expect(l.chromeModel).toBe(cm);
  });

  it('frameAspectWH：landscape 1.414 / portrait 1/1.414（与 prepareExportCanvas 口径一致）', () => {
    expect(frameAspectWH('A4', 'landscape')).toBeCloseTo(1.414, 3);
    expect(frameAspectWH('A4', 'portrait')).toBeCloseTo(1 / 1.414, 6);
    expect(frameAspectWH('screen', 'landscape')).toBeCloseTo(1.414, 3);
  });
});
