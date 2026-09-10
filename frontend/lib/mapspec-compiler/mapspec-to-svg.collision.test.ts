/**
 * V6（ADR-0120 W6）：TS 孪生确定性标签碰撞集成（与 Python 孪生测试同构）。
 * legacy 路径不变；collision 模式标签组置顶 + 诊断上报。
 */
import { describe, expect, it } from 'vitest';
import { compileMapSpecToSvg } from './mapspec-to-svg';

function specWith(labelsCfg: Record<string, unknown> | undefined, features: unknown[]) {
  const spec: Record<string, any> = {
    version: '1.1',
    view: { center: [116, 39], zoom: 10 },
    sources: {
      pts: { type: 'geojson', inlineData: { type: 'FeatureCollection', features } },
    },
    layers: [
      {
        id: 'pts_sym',
        source: 'pts',
        type: 'symbol',
        layout: { 'text-field': '{name}', 'text-size': 14 },
        paint: { 'text-color': '#1e293b' },
      },
    ],
  };
  if (labelsCfg) spec.layout = { labels: labelsCfg };
  return spec;
}

const farApartFeatures = [
  { type: 'Feature', geometry: { type: 'Point', coordinates: [115.8, 38.8] }, properties: {} },
  { type: 'Feature', geometry: { type: 'Point', coordinates: [116.8, 39.6] }, properties: { other: 1 } },
  { type: 'Feature', geometry: { type: 'Point', coordinates: [116.0, 39.0] }, properties: { name: '北京' } },
  { type: 'Feature', geometry: { type: 'Point', coordinates: [116.4, 39.2] }, properties: { name: '廊坊' } },
];

describe('TS 孪生 label collision（W6）', () => {
  it('collision 模式输出顶层 labels 组（置顶）', () => {
    const svg = compileMapSpecToSvg(specWith({ collision: 'deterministic' }, farApartFeatures), {
      targetDpi: 72, width: 400, height: 300, padding: 20,
    });
    expect(svg).toContain('<g class="mapspec-labels">');
    expect(svg.indexOf('mapspec-vector-layers')).toBeLessThan(svg.indexOf('mapspec-labels'));
    // 两点分离 → 两标签都保留
    expect(svg).toContain('北京');
    expect(svg).toContain('廊坊');
  });

  it('legacy 路径无 labels 组（byte-stable 语义）', () => {
    const svg = compileMapSpecToSvg(specWith(undefined, farApartFeatures), {
      targetDpi: 72, width: 400, height: 300, padding: 20,
    });
    expect(svg).not.toContain('mapspec-labels');
    expect(svg).toContain('北京'); // 内联标签仍在
  });

  it('诊断 sink：碰撞抑制上报 label_collision_relaxed', () => {
    const dense = Array.from({ length: 12 }, (_, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116 + i * 0.0001, 39] },
      properties: { name: `点${i}` },
    }));
    const diags: Array<[string, string]> = [];
    compileMapSpecToSvg(specWith({ collision: 'deterministic' }, dense), {
      targetDpi: 72, width: 200, height: 150, padding: 10,
      onDiagnostic: (code, detail) => diags.push([code, detail]),
    });
    expect(diags.some(([c, d]) => c === 'label_collision_relaxed' && d.includes('suppressed='))).toBe(true);
  });

  it('诊断 sink：预算溢出上报 label_budget_exceeded', () => {
    const many = Array.from({ length: 420 }, (_, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116 + (i % 200) * 0.001, 39 + Math.floor(i / 200) * 0.01] },
      properties: { name: `点${i}` },
    }));
    const diags: Array<[string, string]> = [];
    compileMapSpecToSvg(specWith({ collision: 'deterministic' }, many), {
      targetDpi: 72, width: 500, height: 400, padding: 10,
      onDiagnostic: (code, detail) => diags.push([code, detail]),
    });
    expect(diags.some(([c, d]) => c === 'label_budget_exceeded' && d === '400')).toBe(true);
  });
});
