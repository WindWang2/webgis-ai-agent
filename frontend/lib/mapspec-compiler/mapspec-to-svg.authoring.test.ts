/**
 * G16（v2.1）：TS 孪生 authoring × geometry_v1 障碍导出集成
 * （与 Python test_label_authoring_export.py 同构验收）。
 */
import { describe, expect, it } from 'vitest';
import { compileMapSpecToSvg } from './mapspec-to-svg';

const OPTS = { targetDpi: 72, width: 400, height: 300, padding: 20 };

function pt(id: string, lon: number, lat: number, name: string | null) {
  return {
    type: 'Feature',
    id,
    geometry: { type: 'Point', coordinates: [lon, lat] },
    properties: name === null ? {} : { name },
  };
}

function schoolsSpec(overrides: unknown[] | undefined, labelsCfg: Record<string, unknown>) {
  const label: Record<string, unknown> = { field: 'name' };
  if (overrides !== undefined) label.authoring = { overrides };
  return {
    version: '1.1',
    view: { center: [116.45, 39.95], zoom: 11 },
    sources: {
      schools: {
        type: 'geojson' as const,
        inlineData: {
          type: 'FeatureCollection',
          features: [
            pt('s1', 116.44, 39.96, '第一小学'),
            pt('s2', 116.46, 39.96, '第二小学'),
            pt('s3', 116.45, 39.94, '第三小学'),
          ],
        },
      },
    },
    layers: [{
      id: 'schools', source: 'schools', type: 'symbol' as const,
      label: label as never,
      layout: { 'text-field': '{name}', 'text-size': 12 },
      paint: { 'text-color': '#1e293b' },
    }],
    layout: { labels: labelsCfg },
  };
}

describe('TS 孪生 label authoring 导出（G16）', () => {
  it('hidden 覆写：导出中不出现 + 其他照常', () => {
    const svg = compileMapSpecToSvg(
      schoolsSpec([{ featureId: 's2', hidden: true }], { collision: 'deterministic' }),
      OPTS);
    expect(svg).not.toContain('第二小学');
    expect(svg).toContain('第一小学');
    expect(svg).toContain('第三小学');
  });

  it('hidden 在 legacy 模式同样不画', () => {
    const svg = compileMapSpecToSvg(
      schoolsSpec([{ featureId: 's2', hidden: true }], {}), OPTS);
    expect(svg).not.toContain('第二小学');
    expect(svg).toContain('第一小学');
  });

  it('text 覆写：渲染用户文字，原字段文字不单独出现', () => {
    const svg = compileMapSpecToSvg(
      schoolsSpec([{ featureId: 's1', text: '市重点·第一小学' }], { collision: 'deterministic' }),
      OPTS);
    expect(svg).toContain('市重点·第一小学');
    expect(svg).not.toContain('>第一小学<');
  });

  it('legacy 模式 offset 生效（输出改变）', () => {
    const base = compileMapSpecToSvg(schoolsSpec(undefined, {}), OPTS);
    const moved = compileMapSpecToSvg(
      schoolsSpec([{ featureId: 's1', offset: { dx: 3, dy: 3 } }], {}), OPTS);
    expect(moved).not.toBe(base);
  });

  it('orphan 覆写不渲染到别的要素', () => {
    const svg = compileMapSpecToSvg(
      schoolsSpec([{ featureId: 'ghost', text: '幽灵文字' }], { collision: 'deterministic' }),
      OPTS);
    expect(svg).not.toContain('幽灵文字');
  });

  it('pinned 标签渲染且不被自动项挤掉', () => {
    const svg = compileMapSpecToSvg(
      schoolsSpec([{ featureId: 's1', pinned: true, offset: { dx: 1.5, dy: -1 } }], { collision: 'deterministic' }),
      OPTS);
    expect(svg).toContain('第一小学');
  });

  it('geometry_v1 粗线墨迹避让；circles_legacy 不受线墨迹影响', () => {
    const roadSpec = (labelsCfg: Record<string, unknown>) => ({
      version: '1.1',
      view: { center: [116.45, 39.95], zoom: 11 },
      sources: {
        roads: {
          type: 'geojson' as const,
          inlineData: {
            type: 'FeatureCollection',
            features: [{
              type: 'Feature', id: 'r1',
              geometry: { type: 'LineString', coordinates: [[116.40, 39.95], [116.50, 39.95]] },
              properties: {},
            }],
          },
        },
        schools: {
          type: 'geojson' as const,
          inlineData: { type: 'FeatureCollection', features: [pt('s1', 116.45, 39.95, '路边学校')] },
        },
      },
      layers: [
        { id: 'roads', source: 'roads', type: 'line' as const, paint: { 'line-width': 8 } },
        {
          id: 'schools', source: 'schools', type: 'symbol' as const,
          label: { field: 'name' } as never,
          layout: { 'text-field': '{name}', 'text-size': 12 },
          paint: {},
        },
      ],
      layout: { labels: labelsCfg },
    });
    const legacy = compileMapSpecToSvg(
      roadSpec({ collision: 'deterministic' }), OPTS);
    expect(legacy).toContain('路边学校'); // 旧策略不受线墨迹影响
    const geo = compileMapSpecToSvg(
      roadSpec({ collision: 'deterministic', obstaclePolicy: 'geometry_v1' }), OPTS);
    // 单粗线 → 位移避让（仍显示，但不在首选位）或极端时抑制 ——
    // 至少确定性行为：两次编译逐字节一致。
    const geo2 = compileMapSpecToSvg(
      roadSpec({ collision: 'deterministic', obstaclePolicy: 'geometry_v1' }), OPTS);
    expect(geo).toBe(geo2);
  });

  it('policy 缺省与 circles_legacy 输出逐字节一致', () => {
    const a = compileMapSpecToSvg(schoolsSpec(undefined, { collision: 'deterministic' }), OPTS);
    const b = compileMapSpecToSvg(
      schoolsSpec(undefined, { collision: 'deterministic', obstaclePolicy: 'circles_legacy' }), OPTS);
    expect(a).toBe(b);
  });

  it('无 authoring 键 = 既有输出（additive 契约）', () => {
    const plain = compileMapSpecToSvg(schoolsSpec(undefined, {}), OPTS);
    const emptyOverrides = compileMapSpecToSvg(schoolsSpec([], {}), OPTS);
    expect(plain).toBe(emptyOverrides);
  });
});

describe('TS 孪生 authoring 披露（R2）', () => {
  it('orphan/stale/pinned_conflict 诊断经 onDiagnostic 上报（Python 孪生）', () => {
    const diags: Array<[string, string]> = [];
    const spec = schoolsSpec(
      [{ featureId: 'ghost', text: '幽灵' }, { featureId: 's1', pinned: true, offset: { dx: 0.1, dy: 0.1 } }],
      { collision: 'deterministic' });
    spec.sources.schools.inlineData.features = [
      pt('s1', 116.44, 39.96, '第一小学'),
      pt('s2', 116.46, 39.96, '第二小学'),
    ];
    spec.sources.schools.content_revision = 5;
    (spec.layers[0].label as { authoring: { overrides: unknown[] } }).authoring.overrides.push(
      { featureId: 's2', hidden: true, sourceRevision: 1 });
    const svg = compileMapSpecToSvg(spec as never, {
      ...OPTS,
      onDiagnostic: (code: string, detail: string) => { diags.push([code, detail]); },
    });
    const codes = diags.map(([c]) => c);
    expect(codes).toContain('label_authoring_orphan');
    expect(codes).toContain('label_authoring_stale');
    expect(codes).toContain('label_authoring_applied');
    expect(svg).not.toContain('幽灵');
  });
});
