/**
 * G41 canonical 图例三端适配器：live DOM(ThematicLegend) / Canvas(PNG) /
 * 真矢量 SVG。断言同一批数据、同一版本表达在每端都给出矩阵格子、精确计数、
 * 具名误差量与缺失说明；SVG 不再把矩阵省略掉还宣称成功。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { drawChromeLegend } from './export-chrome';
import { buildVectorSvgExport } from './vector-svg-export';
import { buildPublicationLayout } from '../export/layout-description';
import type { ExportChromeModel } from './export-chrome';
import { ThematicLegend } from '@/components/map/thematic-legend';
import type { LegendSpec } from './types';

const CELLS = [6, 7, 8, 3, 4, 5, 0, 1, 2].map((classIndex, i) => ({
  screen_row: Math.floor(i / 3), screen_col: i % 3,
  row: 2 - Math.floor(i / 3), col: i % 3,
  class_index: classIndex, color: `#${(i + 1) * 111111}`.slice(0, 7),
  count: i + 1, empty: false,
  row_interval: { label: `[0, 10) %` }, col_interval: { label: `[0, 10) 人` },
}));

const BIVARIATE = {
  type: 'bivariate', schema_version: 'g41_legend.v1',
  title: '收入 × 比率', n: 3, matrix: 'custom',
  colors: CELLS.map((c) => c.color),
  label_a: '收入', label_b: '比率',
  cells: CELLS,
  axes: {
    row: { field: 'rate', label: '比率', unit: '%', method: 'equal_interval', arrow: 'up', actual_classes: 3, intervals: [] },
    col: { field: 'income', label: '收入', unit: '人', method: 'quantiles', arrow: 'right', actual_classes: 3, intervals: [] },
  },
  counts: { valid_pairs: 45 },
  missing: { label: '无数据', count: 2, policy: '独立无数据表达' },
  boundary_rule: 'upper_inclusive',
  source: { ref_id: 'ref:abc', content_revision: 7, content_fingerprint: 'g41f-x', plan_digest: 'p1' },
  approximate: false,
} as unknown as LegendSpec;

const RELIABILITY = {
  type: 'reliability_dual_channel', schema_version: 'g41_legend.v1',
  title: '估计值与标准误',
  estimate: { label: '估计', unit: 'm', palette_colors: ['#fff', '#aaa', '#333'] },
  measure: {
    kind: 'standard_error', level: null, author_declared: true,
    unit_note: '标准误（m）；与估计值同量纲',
    visual_meaning: '透明度：误差越大越透明（alpha 越低）',
    source: 'author_declared',
  },
  channel: { kind: 'opacity', clip: 'clamp' },
  buckets: [
    { key: 'b0', label: '误差 0–0.5 m', output: 0.9, count: 3 },
    { key: 'b1', label: '误差 0.5–1 m', output: 0.6, count: 2 },
    { key: 'unknown', label: '误差缺失/未知', output: 0.5, count: 1 },
  ],
  missing: { label: '误差缺失/未知', count: 1, policy: '独立表达' },
  source: { ref_id: 'ref:abc', content_revision: 7, content_fingerprint: 'g41f-x', plan_digest: 'p2' },
  approximate: false,
} as unknown as LegendSpec;

// ── Canvas (PNG) ─────────────────────────────────────────────────────────
function mockCtx() {
  const calls: Array<{ op: string; args: unknown[] }> = [];
  const ctx: Record<string, unknown> = {
    fillText: (...a: unknown[]) => calls.push({ op: 'fillText', args: a }),
    fillRect: (...a: unknown[]) => calls.push({ op: 'fillRect', args: a }),
    strokeRect: () => {}, beginPath: () => {}, moveTo: () => {}, lineTo: () => {},
    arc: () => {}, arcTo: () => {}, closePath: () => {}, fill: () => {},
    stroke: () => {}, save: () => {}, restore: () => {}, rotate: () => {},
    translate: () => {}, createLinearGradient: () => ({ addColorStop: () => {} }),
    measureText: () => ({ width: 50 }),
  };
  Object.defineProperties(ctx, {
    fillStyle: { set: (v: unknown) => calls.push({ op: 'fillStyle', args: [v] }), get: () => '' },
    font: { set: (v: unknown) => calls.push({ op: 'font', args: [v] }), get: () => '' },
    textAlign: { set: () => {}, get: () => '' },
    globalAlpha: { set: () => {}, get: () => 1 },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

const DRAW_BASE = {
  darkMode: false, scalePx: (v: number) => v, targetW: 1600, targetH: 1200,
  style: { fontFamily: 'sans-serif', accentColor: '#3182bd', marginPx: 40 },
};

describe('G41 Canvas 图例适配器', () => {
  it('canonical 双变量：逐格精确计数 + 单位/有效对/缺失/边界规则', () => {
    const { ctx, calls } = mockCtx();
    drawChromeLegend({ ctx, ...DRAW_BASE } as never,
      { kind: 'legend', anchor: 'bottom-left', legendSpec: BIVARIATE } as never,
      { marginX: 40 });
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toContain('1');
    expect(texts).toContain('9');
    expect(texts.some((s) => s.startsWith('↑'))).toBe(true);
    expect(texts.some((s) => s.includes('pairs=45') && s.includes('人/%')
      && s.includes('upper_inclusive'))).toBe(true);
    const squares = calls.filter((c) => c.op === 'fillRect' && c.args[2] === 14);
    expect(squares.length).toBe(9);
  });

  it('canonical 可靠性：具名误差量 + level/units + 缺失，不是无名色条', () => {
    const { ctx, calls } = mockCtx();
    drawChromeLegend({ ctx, ...DRAW_BASE } as never,
      { kind: 'legend', anchor: 'bottom-left', legendSpec: RELIABILITY } as never,
      { marginX: 40 });
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts.some((s) => s.includes('standard_error'))).toBe(true);
    expect(texts.some((s) => s.includes('误差缺失/未知'))).toBe(true);
    expect(texts.some((s) => s.includes('clip=clamp'))).toBe(true);
  });
});

// ── 真矢量 SVG ───────────────────────────────────────────────────────────
const SPEC = {
  sources: { s1: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } } },
  layers: [{ id: 'poly', type: 'fill', source: 's1', paint: { 'fill-color': '#2563eb' } }],
};

function layoutFor(legendSpec: LegendSpec) {
  const chromeModel = {
    fromSpec: true, degradations: [], colorbars: [], insets: [], panels: [],
    legends: [{ kind: 'legend', anchor: 'bottom-left', legendSpec }],
  } as unknown as ExportChromeModel;
  return buildPublicationLayout({
    paperSize: 'screen', orientation: 'landscape', dpi: 96,
    frame: { width: 1200, height: 800 }, chromeModel,
  });
}

describe('G41 矢量 SVG 导出', () => {
  it('真实绘制 n×n 矩阵（不再只发省略诊断）', () => {
    const result = buildVectorSvgExport({ spec: SPEC, layout: layoutFor(BIVARIATE) });
    expect(result.svg).toContain('data-g41-type="bivariate"');
    expect(result.svg).toContain('data-g41-cell="6"');
    expect(result.svg).toContain('data-g41-count="1"');
    expect(result.svg).toContain('pairs=45');
    expect(result.degradations.some((d) => d.detail?.includes('bivariate matrix omitted')))
      .toBe(false);
  });

  it('真实绘制可靠性图例（误差量名/level/缺失具名）', () => {
    const result = buildVectorSvgExport({ spec: SPEC, layout: layoutFor(RELIABILITY) });
    expect(result.svg).toContain('data-g41-type="reliability_dual_channel"');
    expect(result.svg).toContain('measure standard_error');
    expect(result.svg).toContain('data-g41-bucket="unknown"');
  });

  it('旧 bivariate（无 canonical cells）仍如实披露省略，不假装已支持', () => {
    const legacy = { type: 'bivariate', n: 3, colors: CELLS.map((c) => c.color),
      label_a: 'a', label_b: 'b', matrix: 'x', breaks_a: [], breaks_b: [] } as unknown as LegendSpec;
    const result = buildVectorSvgExport({ spec: SPEC, layout: layoutFor(legacy) });
    expect(result.degradations.some((d) => d.code === 'legend_entries_truncated'))
      .toBe(true);
  });
});

// ── live DOM（chat 结果卡路径）──────────────────────────────────────────
describe('G41 live 图例', () => {
  it('可靠性图例显示具体误差量与缺失，不伪造置信度', () => {
    render(React.createElement(ThematicLegend, { spec: RELIABILITY }));
    expect(screen.getByTestId('thematic-reliability-legend')).toBeInTheDocument();
    expect(screen.getByText(/标准误（m）/)).toBeInTheDocument();
    expect(screen.getByText(/误差越大越透明/)).toBeInTheDocument();
  });
});
