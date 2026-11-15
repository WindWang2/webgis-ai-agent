/**
 * G40 前端契约测试：参数归一镜像 + image id 稳定性 + TS SVG 孪生语义。
 *
 * 与 Python 端（tests/cartography/test_g40_symbol_assets.py）共享语义：
 * bounds/词表逐字段一致；渲染投影（defs/use/pattern）结构断言。
 */
import { describe, expect, it } from 'vitest';

import {
  ANCHORS, PointParams, TextureParams,
  anchorOffset, normalizePointParams, normalizeTextureParams,
  patternImageId, pointImageId,
} from './contracts';
import { projectBindingStatic } from '../mapspec-compiler/compiler';
import { compileMapSpecToSvg } from '../mapspec-compiler/mapspec-to-svg';
import { BUILTIN_DOCUMENTS } from './builtin-documents';

describe('normalizePointParams（镜像 Python bounds）', () => {
  it('applies defaults and normalizes', () => {
    const p = normalizePointParams({});
    expect(p.size_px).toBe(16);
    expect(p.anchor).toBe('center');
    expect(p.rotation_deg).toBe(0);
    expect(p.opacity).toBe(1);
  });
  it('rejects zero/negative/huge size (S10)', () => {
    expect(() => normalizePointParams({ size_px: 0 })).toThrow();
    expect(() => normalizePointParams({ size_px: -3 })).toThrow();
    expect(() => normalizePointParams({ size_px: 1e9 })).toThrow();
    expect(() => normalizePointParams({ size_px: '16' as never })).toThrow();
  });
  it('rejects map alignment (未实现组合显式拒绝)', () => {
    expect(() => normalizePointParams({ rotation_alignment: 'map' })).toThrow(
      /UNSUPPORTED_ALIGNMENT/);
  });
  it('rejects unknown anchor/params', () => {
    expect(() => normalizePointParams({ anchor: 'diagonal' as never })).toThrow();
    expect(() => normalizePointParams({ bogus: 1 } as never)).toThrow();
  });
});

describe('normalizeTextureParams（镜像 Python bounds）', () => {
  it('rejects out-of-range spacing/stroke and named colors', () => {
    expect(() => normalizeTextureParams({ spacing_px: 0 })).toThrow();
    expect(() => normalizeTextureParams({ spacing_px: 65 })).toThrow();
    expect(() => normalizeTextureParams({ stroke_width_px: 0.1 })).toThrow();
    expect(() => normalizeTextureParams({ bg_color: 'red' })).toThrow();
  });
  it('transparent background is a first-class value (S13)', () => {
    expect(normalizeTextureParams({ bg_color: 'transparent' }).bg_color)
      .toBe('transparent');
    expect(normalizeTextureParams({}).fg_color).toBe('#334155');
  });
});

describe('anchorOffset（与 Python anchor_offset 同式）', () => {
  it('computes anchor displacement from center', () => {
    const doc = BUILTIN_DOCUMENTS['cartoasset.builtin.triangle'];
    expect(anchorOffset(doc, 'center')).toEqual([0, 0]);
    expect(anchorOffset(doc, 'top-left')).toEqual([-12, -12]);
    expect(anchorOffset(doc, 'bottom')).toEqual([0, 12]);
    expect(() => anchorOffset(doc, 'nope' as never)).not.toThrow(); // hx/vy 未命中 → 0
  });
});

describe('image id 稳定性（注册/defs 去重键）', () => {
  it('same digest+params → same id; different params → different id', () => {
    const params = { size_px: 16, anchor: 'center', rotation_deg: 0, opacity: 1 };
    expect(pointImageId('sha256:' + 'a'.repeat(64), params))
      .toBe(pointImageId('sha256:' + 'a'.repeat(64), params));
    expect(pointImageId('sha256:' + 'a'.repeat(64), params))
      .not.toBe(pointImageId('sha256:' + 'a'.repeat(64), { ...params, size_px: 20 }));
    expect(patternImageId('sha256:' + 'b'.repeat(64), { spacing_px: 8 }))
      .toMatch(/^cartoap-/);
  });
});

describe('projectBindingStatic（碰撞合同/类别映射）', () => {
  const tri = BUILTIN_DOCUMENTS['cartoasset.builtin.triangle'];
  const binding = {
    schema_version: 1 as const,
    asset_id: 'cartoasset.builtin.triangle', revision: 1,
    content_digest: tri.content_digest, kind: 'point_symbol' as const,
    params: { size_px: 18, anchor: 'center', rotation_deg: 90, opacity: 1 },
  };
  it('S31: explicit allow-overlap + ignore-placement; viewport alignment', () => {
    const proj = projectBindingStatic(binding, 'pts');
    expect(proj.failure).toBeUndefined();
    expect(proj.typeOverride).toBe('symbol');
    expect(proj.layout['icon-allow-overlap']).toBe(true);
    expect(proj.layout['icon-ignore-placement']).toBe(true);
    expect(proj.layout['icon-rotation-alignment']).toBe('viewport');
    expect(proj.layout['icon-rotate']).toBe(90);
  });
  it('declutter explicitly rejected (未实现组合)', () => {
    const proj = projectBindingStatic(
      { ...binding, declutter: true } as never, 'pts');
    expect(proj.failure?.code).toBe('UNSUPPORTED_DECLUTTER');
  });
  it('texture: fill-pattern + fill-color removal (S13 背景合同在参数)', () => {
    const hatch = BUILTIN_DOCUMENTS['cartoasset.builtin.hatch'];
    const proj = projectBindingStatic({
      schema_version: 1 as const,
      asset_id: 'cartoasset.builtin.hatch', revision: 1,
      content_digest: hatch.content_digest, kind: 'area_texture' as const,
      params: { spacing_px: 10, angle_deg: 45, stroke_width_px: 1.5,
                pattern_scale: 1, fg_color: '#334155', bg_color: '#fef08a' },
    }, 'zones');
    expect(proj.paint['fill-pattern']).toMatch(/^cartoap-/);
    expect(proj.removePaintKeys).toContain('fill-color');
  });
  it('S02: category icon expression keeps typed keys (不并类)', () => {
    const sq = BUILTIN_DOCUMENTS['cartoasset.builtin.square'];
    const proj = projectBindingStatic({
      ...binding,
      category_binding: {
        field: 'kind', key_type: 'number',
        map: [{ key: 1, asset_id: 'cartoasset.builtin.square', revision: 1,
                content_digest: sq.content_digest }],
        other_mode: 'keep_current' as const,
      },
    }, 'pts');
    const expr = proj.layout['icon-image'] as unknown[];
    expect(JSON.stringify(expr)).toContain('"match"');
    expect(JSON.stringify(expr)).not.toContain('"1"'); // 数值 1 不字符串化
  });
});

describe('TS SVG 孪生（mapspec-to-svg 资产消费）', () => {
  it('emits defs/use/pattern; no legacy circle for bound layers (S01/S23)', () => {
    const tri = BUILTIN_DOCUMENTS['cartoasset.builtin.triangle'];
    const hatch = BUILTIN_DOCUMENTS['cartoasset.builtin.hatch'];
    const docs = new Map([
      [tri.content_digest, tri],
      [hatch.content_digest, hatch],
    ]);
    const spec = {
      version: '2.1', view: { center: [116.4, 39.9], zoom: 10 },
      sources: {
        pts: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [
          { type: 'Feature', properties: {}, geometry: {
            type: 'Point', coordinates: [116.39, 39.91] } },
        ] } },
        zones: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [
          { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [
            [[116.38, 39.88], [116.42, 39.88], [116.42, 39.92], [116.38, 39.92], [116.38, 39.88]]] } },
        ] } },
      },
      layers: [
        { id: 'zones', source: 'zones', type: 'fill', paint: { color: '#60a5fa' },
          symbol_asset: { schema_version: 1, asset_id: 'cartoasset.builtin.hatch',
            revision: 1, content_digest: hatch.content_digest,
            kind: 'area_texture', params: { spacing_px: 10, angle_deg: 45,
              stroke_width_px: 1.5, pattern_scale: 1, fg_color: '#334155',
              bg_color: 'transparent' } } },
        { id: 'pts', source: 'pts', type: 'circle', paint: { color: '#2563eb' },
          symbol_asset: { schema_version: 1, asset_id: 'cartoasset.builtin.triangle',
            revision: 1, content_digest: tri.content_digest,
            kind: 'point_symbol', params: { size_px: 18, anchor: 'center',
              rotation_deg: 90, opacity: 1 } } },
      ],
    };
    const svg = compileMapSpecToSvg(spec as never, {
      symbolAssets: docs as never,
    });
    expect(svg).toContain('id="cartoasset-');
    expect(svg).toContain('<use ');
    expect(svg).toContain('url(#cartoap-');
    expect(svg).not.toContain('<circle cx='); // 绑定替代默认圆
    // 缺资产：不画 + 诊断（不伪装）
    const diags: string[] = [];
    const svg2 = compileMapSpecToSvg(spec as never, {
      symbolAssets: new Map() as never,
      onDiagnostic: (code) => diags.push(code),
    });
    expect(diags).toContain('symbol_asset_unavailable');
    expect(svg2).not.toContain('<use ');
    expect(svg2).not.toContain('<circle cx=');
  });
});

describe('rationalLineLattice（F3：live 纹理 = SVG patternTransform 语义）', () => {
  it('seamless lattice: 45°/30°/0°/90° angle fidelity + spacing fidelity', async () => {
    const { rationalLineLattice } = await import('./rasterize');
    const cases: Array<[number, number, number]> = [
      // [thetaDeg, d, maxAngleErrDeg]
      [0, 24, 0.01],
      [45, 24, 0.3],
      [30, 24, 0.5],
      [-45, 16, 0.3],
      [90, 24, 0.01],
      [60, 20, 0.5],
    ];
    for (const [theta, d, maxErr] of cases) {
      const { p, q, size } = rationalLineLattice(theta, d);
      const hyp = Math.hypot(p, q);
      // 角度保真（有理斜率逼近）
      const angleErr = Math.abs((Math.atan2(p, q) * 180) / Math.PI - theta);
      expect(angleErr).toBeLessThanOrEqual(maxErr + 1e-9);
      // 间距保真：d_eff = size/hyp ≈ d（亚像素量化）
      const dEff = size / hyp;
      expect(Math.abs(dEff - d)).toBeLessThanOrEqual(1.0);
      // tile 预算
      expect(size).toBeGreaterThanOrEqual(8);
      expect(size).toBeLessThanOrEqual(256);
      // 无缝性由构造保证：x/y 平移 size 使相位各推进 q·d_eff / p·d_eff
      //（整数倍 d_eff）—— 这里锁 q、p 为整数与 d_eff 定义本身。
      expect(Number.isInteger(p)).toBe(true);
      expect(Number.isInteger(q)).toBe(true);
      expect(Math.abs(size - Math.round(d * hyp))).toBeLessThanOrEqual(1);
    }
  });
  it('极端角（0/90）取正交格点（周期恰为 d）', async () => {
    const { rationalLineLattice } = await import('./rasterize');
    expect(rationalLineLattice(0, 24)).toEqual({ p: 0, q: 1, size: 24 });
    const v = rationalLineLattice(90, 24);
    expect(Math.abs(v.p)).toBe(1);
    expect(v.q).toBe(0);
    expect(v.size).toBe(24);
  });
});
