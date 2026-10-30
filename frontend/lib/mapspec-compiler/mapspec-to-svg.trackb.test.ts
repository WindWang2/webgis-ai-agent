/**
 * Track B（M9）导出标注收敛的 TS 孪生测试 —— 与
 * tests/cartography/test_label_trackb_vnext.py 同 fixture 断言同 invariants：
 * - priorityField 重要度名次（数值大者先保；缺省收集序）；
 * - CJK 折行（≤2 行 tspan；短名单行）；
 * - 线 repeat（symbol-placement=line 沿线 ≤4 贴；缺省单锚）。
 */
import { describe, expect, it } from 'vitest';
import { compileMapSpecToSvg as mapspecToSvg } from './mapspec-to-svg';
import { wrapLabelMultilingual, estimateLabelBox } from '@/lib/label-geometry';

function baseSpec(features: unknown[], layerExtra?: Record<string, unknown>) {
  const spreaders = [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [115.8, 38.8] }, properties: {} },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [116.8, 39.6] }, properties: { other: 1 } },
  ];
  return {
    version: '1.1',
    view: { center: [116.0, 39.0], zoom: 10 },
    sources: {
      pts: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [...spreaders, ...features] } },
    },
    layers: [
      {
        id: 'pts_sym', source: 'pts', type: 'symbol',
        layout: { 'text-field': '{name}', 'text-size': 14 },
        paint: { 'text-color': '#1e293b' },
        ...(layerExtra ?? {}),
      },
    ],
    layout: { labels: { collision: 'deterministic' } },
  };
}

function stackedPoints(n: number) {
  return Array.from({ length: n }, (_, rank) => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [116.0, 39.0] },
    properties: { name: `点${String(rank).padStart(2, '0')}`, rank },
  }));
}

function labelsBody(svg: string): string {
  const idx = svg.indexOf('class="mapspec-labels"');
  expect(idx).toBeGreaterThan(-1);
  return svg.slice(idx);
}

describe('export label convergence (Track B, TS twin)', () => {
  it('priorityField: higher importance survives; default keeps collection order', () => {
    const withPf = mapspecToSvg(
      baseSpec(stackedPoints(30), { label: { field: 'name', priorityField: 'rank' } }) as never,
      { targetDpi: 72, width: 200, height: 150, padding: 5 },
    );
    const body = labelsBody(withPf);
    expect(body).toContain('点29');
    expect(body).toContain('点28');
    expect(body).not.toContain('点00');

    const withoutPf = mapspecToSvg(
      baseSpec(stackedPoints(30)) as never,
      { targetDpi: 72, width: 200, height: 150, padding: 5 },
    );
    const body2 = labelsBody(withoutPf);
    expect(body2).toContain('点00');
    expect(body2).not.toContain('点29');
  });

  it('CJK wrap: long name → ≤2 tspan lines; short name stays single line', () => {
    const longName = '成都市实验小学附属外国语学校';
    const svg = mapspecToSvg(
      baseSpec([
        { type: 'Feature', geometry: { type: 'Point', coordinates: [116.0, 39.0] }, properties: { name: longName } },
      ]) as never,
      { targetDpi: 72, width: 300, height: 200, padding: 10 },
    );
    const body = labelsBody(svg);
    expect(body).toContain('<tspan');
    expect(body).not.toContain(longName); // 不再单行整串

    const short = mapspecToSvg(
      baseSpec([
        { type: 'Feature', geometry: { type: 'Point', coordinates: [116.0, 39.0] }, properties: { name: '北京' } },
      ]) as never,
      { targetDpi: 72, width: 300, height: 200, padding: 10 },
    );
    expect(labelsBody(short)).not.toContain('<tspan');
    expect(labelsBody(short)).toContain('北京');
  });

  it('line repeat: symbol-placement=line multi-places (≤4); default single anchor', () => {
    const feats = [
      {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [[115.6, 39.0], [115.8, 39.0], [116.0, 39.0], [116.2, 39.0], [116.4, 39.0]] },
        properties: { name: '成渝高速' },
      },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [115.6, 38.8] }, properties: {} },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [116.4, 39.3] }, properties: {} },
    ];
    const spec = (placement?: string) => ({
      version: '1.1',
      view: { center: [116.0, 39.05], zoom: 9 },
      sources: { ln: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: feats } } },
      layers: [{
        id: 'ln_sym', source: 'ln', type: 'symbol',
        layout: { 'text-field': '{name}', 'text-size': 14, ...(placement ? { 'symbol-placement': placement } : {}) },
        paint: { 'text-color': '#1e293b' },
        label: { field: 'name' },
      }],
      layout: { labels: { collision: 'deterministic' } },
    });
    const repeated = mapspecToSvg(spec('line') as never, { targetDpi: 72, width: 1000, height: 200, padding: 10 });
    const count = (labelsBody(repeated).match(/成渝高速/g) ?? []).length;
    expect(count).toBeGreaterThanOrEqual(2);
    expect(count).toBeLessThanOrEqual(4);

    const single = mapspecToSvg(spec() as never, { targetDpi: 72, width: 1000, height: 200, padding: 10 });
    expect((labelsBody(single).match(/成渝高速/g) ?? []).length).toBe(1);
  });

  it('wrapLabelMultilingual mirrors Python constants (CJK char / latin word / auto)', () => {
    // 13 窄字符位预算：8 CJK 字（8em > 7.8em）→ 折行；4 字 → 单行。
    expect(wrapLabelMultilingual('成都市实验小学附属外国语学校', 13, 2)).toHaveLength(2);
    expect(wrapLabelMultilingual('成都小学', 13, 2)).toHaveLength(1);
    // 拉丁按词：每行是完整词序列（词中间不断开）。
    const latin = wrapLabelMultilingual('International Business District', 13, 2);
    expect(latin.every((ln) => ln.split(' ').every((w2) => w2.length <= 13))).toBe(true);
    expect(latin.join(' ')).toContain('International');
    // 多行盒：宽=最宽行、高=行数×1.2em（与 Python estimate_label_box 同口径）。
    const [w, h] = estimateLabelBox('成都市\n实验', 14);
    const [wSingle] = estimateLabelBox('成都市', 14);
    expect(w).toBeCloseTo(wSingle, 10);
    expect(h).toBeCloseTo(2 * 14 * 1.2, 10);
  });
});
