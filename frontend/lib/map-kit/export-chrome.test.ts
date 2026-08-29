/**
 * Export chrome（ADR-0081 Export Parity）—— spec 驱动的 chrome 模型构建
 * 与画布绘制契约：placement 语义、组件 enabled 门控、图表/统计卡导出、
 * 指北针旋转符号、色条量化。
 */
import { describe, expect, it, vi } from 'vitest';
import {
  anchorOrigin,
  buildExportChrome,
  drawChromeAttribution,
  drawChromeChartPanel,
  drawChromeColorbar,
  drawChromeLegend,
  drawChromeNorthArrow,
  drawChromeScaleBar,
  drawChromeStatsPanel,
  drawChromeText,
} from './export-chrome';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

function comp(partial: Partial<MapSpecComponent> & { id: string; type: MapSpecComponent['type'] }): MapSpecComponent {
  return partial as MapSpecComponent;
}

const CANVAS = { width: 1600, height: 1200 };
const VIEWPORT = { width: 800, height: 600 };

function specOf(components: MapSpecComponent[]) {
  return { layout: { components } };
}

describe('buildExportChrome — 模型构建', () => {
  it('无 spec 组件 → fromSpec=false（exporter 走 legacy 槽位）', async () => {
    const model = await buildExportChrome({ spec: null, viewport: VIEWPORT, legendSpecsByLayer: {} }, CANVAS);
    expect(model.fromSpec).toBe(false);
    expect(model.panels).toEqual([]);
  });

  it('title/subtitle 文本：请求参数 > spec 组件', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([
          comp({ id: 'title', type: 'title', options: { text: 'spec 标题' } }),
          comp({ id: 'subtitle', type: 'subtitle', options: { text: 'spec 副标题' } }),
        ]),
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
        requestTitle: '请求标题',
      },
      CANVAS,
    );
    expect(model.title?.text).toBe('请求标题');
    expect(model.subtitle?.text).toBe('spec 副标题');
  });

  it('anchor 从组件解析（title top-center 与 live 一致）', async () => {
    const model = await buildExportChrome(
      { spec: specOf([comp({ id: 'title', type: 'title', options: { text: 'T' } })]), viewport: VIEWPORT, legendSpecsByLayer: {} },
      CANVAS,
    );
    expect(model.title?.anchor).toBe('top-center');
  });

  it('floating 坐标按画布/视口比例缩放', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([
          comp({ id: 'chart', type: 'chart_panel', placement: { mode: 'floating', x: 100, y: 50, width: 300, height: 200 }, options: { chart: { type: 'bar', title: 'C', data: [{ name: 'a', value: 1 }] } } }),
        ]),
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    expect(model.panels[0]?.rect).toEqual({ x: 200, y: 100, width: 600, height: 400 });
  });

  it('禁用的图例组件不出现在导出（enabled 门控 parity）', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([comp({ id: 'legend-main', type: 'legend', enabled: false, options: { layerId: 'poi' } })]),
        viewport: VIEWPORT,
        legendSpecsByLayer: { poi: { type: 'graduated', field: 'f', breaks: [0, 1], palette: 'Blues', palette_colors: ['#eff3ff', '#08519c'] } as any },
        fallbackLegendSpec: { type: 'graduated', field: 'g', breaks: [0, 1], palette: 'Blues', palette_colors: ['#eff3ff', '#08519c'] } as any,
      },
      CANVAS,
    );
    expect(model.legend).toBeUndefined();
  });

  it('启用的图例组件绑定 layerId 的 legend_spec', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([comp({ id: 'legend-main', type: 'legend', options: { layerId: 'poi' } })]),
        viewport: VIEWPORT,
        legendSpecsByLayer: { poi: { type: 'graduated', field: '密度', breaks: [0, 1, 2], palette: 'Blues', palette_colors: ['#eff3ff', '#6baed6', '#08519c'] } as any },
      },
      CANVAS,
    );
    expect(model.legend?.legendSpec?.field).toBe('密度');
    expect(model.legend?.anchor).toBe('bottom-left');
  });

  it('色条组件绑定连续 legend_spec', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([comp({ id: 'colorbar-main', type: 'continuous_colorbar', options: { layerId: 'heat' } })]),
        viewport: VIEWPORT,
        legendSpecsByLayer: { heat: { type: 'continuous', min: 0, max: 100, palette: 'YlOrRd', palette_colors: ['#ffffb2', '#f03b20'], unit: '个/km²' } as any },
      },
      CANVAS,
    );
    expect(model.colorbar?.legendSpec).toMatchObject({ min: 0, max: 100, unit: '个/km²' });
  });

  it('统计卡数据解析（options.stats）', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([
          comp({ id: 'stats', type: 'statistics_panel', options: { stats: { title: '统计', items: [{ label: '学校', value: 120, unit: '所' }] } } }),
        ]),
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    expect(model.panels[0]?.stats).toEqual({ title: '统计', items: [{ label: '学校', value: 120, unit: '所' }] });
  });

  it('chartRef 经 loadChart 拉取（与 live 同一 artifact 协议）', async () => {
    const loadChart = vi.fn().mockResolvedValue({ type: 'pie', title: '占比', data: [{ name: 'a', value: 3 }] });
    const model = await buildExportChrome(
      {
        spec: specOf([comp({ id: 'chart', type: 'chart_panel', options: { chartRef: 'ref:chart-1' } })]),
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
        loadChart,
      },
      CANVAS,
    );
    expect(loadChart).toHaveBeenCalledWith('ref:chart-1');
    expect(model.panels[0]?.chart?.type).toBe('pie');
  });

  it('chartRef 拉取失败 → 面板缺席（无数据不伪造）', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([comp({ id: 'chart', type: 'chart_panel', options: { chartRef: 'ref:chart-x' } })]),
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
        loadChart: () => Promise.reject(new Error('gone')),
      },
      CANVAS,
    );
    expect(model.panels).toEqual([]);
  });

  it('署名组件文本进入模型', async () => {
    const model = await buildExportChrome(
      {
        spec: specOf([comp({ id: 'attribution', type: 'attribution', options: { text: '© Data Corp' } })]),
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    expect(model.attribution?.text).toBe('© Data Corp');
    expect(model.attribution?.anchor).toBe('bottom-left');
  });
});

// ── 画布绘制（mock ctx，锁定坐标/旋转/颜色契约）─────────────────────

function mockCtx() {
  const calls: Array<{ op: string; args: unknown[] }> = [];
  const ctx: any = {
    fillText: (...a: unknown[]) => calls.push({ op: 'fillText', args: a }),
    fillRect: (...a: unknown[]) => calls.push({ op: 'fillRect', args: a }),
    strokeRect: (...a: unknown[]) => calls.push({ op: 'strokeRect', args: a }),
    rotate: (...a: unknown[]) => calls.push({ op: 'rotate', args: a }),
    translate: (...a: unknown[]) => calls.push({ op: 'translate', args: a }),
    beginPath: (...a: unknown[]) => calls.push({ op: 'beginPath', args: a }),
    moveTo: (...a: unknown[]) => calls.push({ op: 'moveTo', args: a }),
    lineTo: (...a: unknown[]) => calls.push({ op: 'lineTo', args: a }),
    arc: (...a: unknown[]) => calls.push({ op: 'arc', args: a }),
    arcTo: (...a: unknown[]) => calls.push({ op: 'arcTo', args: a }),
    closePath: (...a: unknown[]) => calls.push({ op: 'closePath', args: a }),
    fill: (...a: unknown[]) => calls.push({ op: 'fill', args: a }),
    stroke: (...a: unknown[]) => calls.push({ op: 'stroke', args: a }),
    save: () => {},
    restore: () => {},
    set fillStyle(v: unknown) { calls.push({ op: 'fillStyle', args: [v] }); },
    get fillStyle() { return ''; },
    set font(v: unknown) { calls.push({ op: 'font', args: [v] }); },
    get font() { return ''; },
    set textAlign(v: unknown) { calls.push({ op: 'textAlign', args: [v] }); },
    get textAlign() { return ''; },
    createLinearGradient: () => ({ addColorStop: () => {} }),
    measureText: () => ({ width: 50 }),
  };
  return { ctx, calls };
}

const DRAW_BASE = {
  darkMode: false,
  scalePx: (v: number) => v,
  targetW: 1600,
  targetH: 1200,
  style: { fontFamily: 'sans-serif', accentColor: '#3182bd', marginPx: 40 } as any,
};

describe('anchorOrigin — 槽位语义', () => {
  it('七槽对齐/所属边正确；y 一律是距所属边的 margin 距离（review P0 契约）', () => {
    const d = { targetW: 1000, targetH: 800, marginX: 40, marginY: 40 };
    expect(anchorOrigin('top-center', d).align).toBe('center');
    expect(anchorOrigin('top-left', d)).toMatchObject({ x: 40, y: 40, align: 'left', vAlign: 'top' });
    // bottom 槽：y=40（距底边），消费端恰好一次 targetH - y 换算
    expect(anchorOrigin('bottom-right', d)).toMatchObject({ x: 960, y: 40, align: 'right', vAlign: 'bottom' });
    expect(anchorOrigin('bottom-left', d)).toMatchObject({ x: 40, y: 40, vAlign: 'bottom' });
    expect(anchorOrigin('bottom-center', d)).toMatchObject({ x: 500, vAlign: 'bottom' });
    expect(anchorOrigin('none', d).align).toBe('left');
  });
});

describe('底部锚点绘制坐标（review P0 回归锁定）', () => {
  it('bottom-right 比例尺画在画布底部（by ≈ targetH - margin）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeScaleBar({ ctx, ...DRAW_BASE }, { kind: 'scale_bar', anchor: 'bottom-right' }, 10, 1, { marginX: 40, marginY: 52 });
    const rect = calls.find((c) => c.op === 'strokeRect');
    expect(rect).toBeDefined();
    const [bx, by, bw, bh] = rect!.args as number[];
    expect(by).toBeGreaterThan(1000); // targetH(1200) - 52 - 8 附近，绝不在顶部
    expect(by + bh).toBeLessThanOrEqual(1200);
    expect(bx + bw).toBeLessThanOrEqual(1600);
    expect(bx).toBeGreaterThan(800);
  });

  it('bottom-left 图例画在画布底部（ly + legendH ≤ targetH）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeLegend(
      { ctx, ...DRAW_BASE },
      {
        kind: 'legend', anchor: 'bottom-left',
        legendSpec: { type: 'graduated', field: '密度', breaks: [0, 10, 20], palette_colors: ['#1', '#2'] } as any,
      },
      { marginX: 40, marginY: 56 },
    );
    const rect = calls.find((c) => c.op === 'fillRect');
    expect(rect).toBeDefined();
    const [, ly] = rect!.args as number[];
    expect(ly).toBeGreaterThan(600); // 底部区域
  });

  it('bottom-left attribution 不与标题区重叠（y 在画布底部）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeAttribution({ ctx, ...DRAW_BASE }, { kind: 'attribution', anchor: 'bottom-left', text: '© X' }, { marginX: 40, marginY: 22 });
    const text = calls.find((c) => c.op === 'fillText');
    expect(text?.args[2]).toBeGreaterThan(1000); // y = targetH - 22（args[1] 是 x）
  });
});

describe('drawChromeText — title 居中（与 live top-center 一致）', () => {
  it('top-center 使用 center 对齐', () => {
    const { ctx, calls } = mockCtx();
    drawChromeText({ ctx, ...DRAW_BASE }, { kind: 'title', anchor: 'top-center', text: 'T' }, 32, '#000', { marginX: 40, marginY: 52 });
    const aligns = calls.filter((c) => c.op === 'textAlign').map((c) => c.args[0]);
    expect(aligns).toContain('center');
    const text = calls.find((c) => c.op === 'fillText');
    expect(text?.args[0]).toBe('T');
    expect(text?.args[1]).toBe(800); // targetW/2（top-center 居中）
  });
});

describe('drawChromeNorthArrow — 旋转符号 parity', () => {
  it('bearing=30 时旋转 -30°（live 是 rotate(-bearing)；旧导出为 +bearing）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeNorthArrow({ ctx, ...DRAW_BASE }, { kind: 'north_arrow', anchor: 'top-right' }, 30, { marginX: 40 });
    const rotate = calls.find((c) => c.op === 'rotate');
    expect(rotate?.args[0]).toBeCloseTo((-30 * Math.PI) / 180, 6);
  });
});

describe('drawChromeScaleBar — bottom-right 槽位', () => {
  it('条形绘制在右下（bx 从右侧回收）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeScaleBar({ ctx, ...DRAW_BASE }, { kind: 'scale_bar', anchor: 'bottom-right' }, 10, 1, { marginX: 40 });
    const rect = calls.find((c) => c.op === 'strokeRect');
    expect(rect).toBeDefined();
    const [bx, , bw] = rect!.args as number[];
    expect(bx + bw).toBeLessThanOrEqual(1600);
    expect(bx).toBeGreaterThan(800); // 右半画布
  });
});

describe('drawChromeColorbar — 渐变 + 量化标签', () => {
  it('min/max/unit 文本绘制', () => {
    const { ctx, calls } = mockCtx();
    drawChromeColorbar(
      { ctx, ...DRAW_BASE },
      {
        kind: 'colorbar', anchor: 'bottom-right',
        legendSpec: { type: 'continuous', min: 0, max: 100, palette_colors: ['#a', '#b'], unit: '个/km²', field: '密度' } as any,
      },
      { marginX: 40 },
    );
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts.some((t) => t.includes('100'))).toBe(true);
    expect(texts.some((t) => t.includes('个/km²'))).toBe(true);
    expect(texts.some((t) => t.toUpperCase() === '密度'.toUpperCase())).toBe(true);
  });
});

describe('drawChromeLegend — 分级图例', () => {
  it('breaks 生成区间标签', () => {
    const { ctx, calls } = mockCtx();
    drawChromeLegend(
      { ctx, ...DRAW_BASE },
      {
        kind: 'legend', anchor: 'bottom-left',
        legendSpec: { type: 'graduated', field: '密度', breaks: [0, 10, 20], palette_colors: ['#1', '#2'] } as any,
      },
      { marginX: 40 },
    );
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts.some((t) => t.includes('0.0 – 10.0'))).toBe(true);
  });
});

describe('drawChromeStatsPanel / drawChromeChartPanel — 面板导出', () => {
  it('统计卡绘制 label/value 行', () => {
    const { ctx, calls } = mockCtx();
    drawChromeStatsPanel(
      { ctx, ...DRAW_BASE },
      { kind: 'statistics', anchor: 'top-left', stats: { title: '统计', items: [{ label: '学校', value: 120, unit: '所' }] } },
      { marginX: 40 },
    );
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toContain('统计');
    expect(texts).toContain('学校');
    expect(texts).toContain('120 所');
  });

  it('bar 图绘制数据条', () => {
    const { ctx, calls } = mockCtx();
    drawChromeChartPanel(
      { ctx, ...DRAW_BASE },
      {
        kind: 'chart', anchor: 'top-left',
        chart: { type: 'bar', title: '分布', data: [{ name: 'a', value: 3 }, { name: 'b', value: 5 }] },
      },
      { marginX: 40 },
    );
    const rects = calls.filter((c) => c.op === 'fillRect');
    expect(rects.length).toBeGreaterThanOrEqual(2); // 两条数据条
  });

  it('pie 图绘制扇形（arc 调用）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeChartPanel(
      { ctx, ...DRAW_BASE },
      {
        kind: 'chart', anchor: 'top-left',
        chart: { type: 'pie', title: '占比', data: [{ name: 'a', value: 1 }, { name: 'b', value: 1 }] },
      },
      { marginX: 40 },
    );
    // 每个扇区一个 arc（中心 moveTo + arc + closePath + fill）
    expect(calls.filter((c) => c.op === 'fill').length).toBeGreaterThanOrEqual(2);
  });
});
