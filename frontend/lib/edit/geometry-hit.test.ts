/**
 * geometry-hit（G24/P2）域内契约：
 * - A08 Polygon 洞内不命中；洞外环内命中；
 * - A09 长线段中段命中（逐段距离，非仅顶点）；
 * - A10 Multi* 真实部件（MultiPolygon 各面外环减洞 / MultiLineString 多折线）；
 * - 点命中 = 顶点像素距离；全部在投影像素空间。
 */
import { describe, expect, it } from 'vitest';
import {
  distToSegmentPx,
  hitParts,
  preciseFeatureHit,
  pointInRing,
} from '@/lib/edit/geometry-hit';

/** 线性投影：经纬 → 像素（测试里 1° = 100px，无旋转）。 */
const project = (p: [number, number]) => ({ x: p[0] * 100, y: -p[1] * 100 });
const P = { x: 104.5 * 100, y: -30.5 * 100 }; // (104.5, 30.5) 的像素位

const lineFeature: GeoJSON.Feature = {
  type: 'Feature',
  id: 1,
  geometry: { type: 'LineString', coordinates: [[104.0, 30.5], [105.0, 30.5]] },
  properties: {},
};

const polygonWithHole: GeoJSON.Feature = {
  type: 'Feature',
  id: 2,
  geometry: {
    type: 'Polygon',
    coordinates: [
      [[104.0, 30.0], [105.0, 30.0], [105.0, 31.0], [104.0, 31.0], [104.0, 30.0]],
      [[104.4, 30.4], [104.6, 30.4], [104.6, 30.6], [104.4, 30.6], [104.4, 30.4]],
    ],
  },
  properties: {},
};

const multiPolygon: GeoJSON.Feature = {
  type: 'Feature',
  id: 3,
  geometry: {
    type: 'MultiPolygon',
    coordinates: [
      [[[104.0, 30.0], [104.2, 30.0], [104.2, 30.2], [104.0, 30.2], [104.0, 30.0]]],
      // 第二部件：带洞（洞覆盖 (104.5, 30.5)）
      [
        [[104.4, 30.4], [104.6, 30.4], [104.6, 30.6], [104.4, 30.6], [104.4, 30.4]],
        [[104.45, 30.45], [104.55, 30.45], [104.55, 30.55], [104.45, 30.55], [104.45, 30.45]],
      ],
    ],
  },
  properties: {},
};

describe('distToSegmentPx', () => {
  it('中点距离=垂距；端点外=到端点距离', () => {
    const a = { x: 0, y: 0 };
    const b = { x: 100, y: 0 };
    expect(distToSegmentPx({ x: 50, y: -7 }, a, b)).closeTo(7, 1e-9);
    expect(distToSegmentPx({ x: -30, y: -40 }, a, b)).closeTo(50, 1e-9);
  });
});

describe('pointInRing', () => {
  it('方形环内/外/顶点重合', () => {
    const ring: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
    expect(pointInRing(5, 5, ring)).toBe(true);
    expect(pointInRing(15, 5, ring)).toBe(false);
    expect(pointInRing(-1, -1, ring)).toBe(false);
  });
});

describe('hitParts', () => {
  it('MultiPolygon → 每个面一个 polygon 部件', () => {
    const parts = hitParts(multiPolygon.geometry);
    expect(parts).toHaveLength(2);
    expect(parts.every((p) => p.kind === 'polygon')).toBe(true);
  });
  it('GeometryCollection → 空部件（不命中）', () => {
    expect(hitParts({ type: 'GeometryCollection', geometries: [] })).toEqual([]);
  });
});

describe('preciseFeatureHit', () => {
  it('A09：长线段中段命中（旧实现只测顶点会漏）', () => {
    const r = preciseFeatureHit(lineFeature, [104.5, 30.5], project, P, 6);
    expect(r.hit).toBe(true);
    expect(r.distPx).toBe(0);
  });

  it('A09：线旁 3px 命中 / 8px 不命中（容差语义）', () => {
    // 纬度差 0.03° = 3px
    const near = preciseFeatureHit(lineFeature, [104.5, 30.53], project, { x: 10450, y: -3053 }, 6);
    expect(near.hit).toBe(true);
    const far = preciseFeatureHit(lineFeature, [104.5, 30.5], project, { x: 10450, y: -3058 }, 6);
    expect(far.hit).toBe(false);
  });

  it('A08：洞内不命中；洞外环内命中；面命中距离为 0', () => {
    const inHole = preciseFeatureHit(polygonWithHole, [104.5, 30.5], project, P, 6);
    expect(inHole.hit).toBe(false);
    const inShell = preciseFeatureHit(polygonWithHole, [104.2, 30.2], project, { x: 10420, y: -3020 }, 6);
    expect(inShell.hit).toBe(true);
    expect(inShell.distPx).toBe(0);
  });

  it('A10：MultiPolygon 第二个部件的洞内不命中、环间命中', () => {
    const inPart2Hole = preciseFeatureHit(multiPolygon, [104.5, 30.5], project, P, 6);
    expect(inPart2Hole.hit).toBe(false); // (104.5,30.5) 在第二部件的洞里
    const inPart1 = preciseFeatureHit(multiPolygon, [104.1, 30.1], project, { x: 10410, y: -3010 }, 6);
    expect(inPart1.hit).toBe(true);
  });

  it('MultiLineString 中段命中 + MultiPoint 顶点命中', () => {
    const ml: GeoJSON.Feature = {
      type: 'Feature',
      geometry: { type: 'MultiLineString', coordinates: [[[104.0, 30.0], [104.0, 31.0]], [[105.0, 30.0], [106.0, 30.0]]] },
      properties: {},
    };
    expect(preciseFeatureHit(ml, [104.0, 30.5], project, { x: 10400, y: -3050 }, 6).hit).toBe(true);
    const mp: GeoJSON.Feature = {
      type: 'Feature',
      geometry: { type: 'MultiPoint', coordinates: [[104.5, 30.5], [105.0, 30.0]] },
      properties: {},
    };
    expect(preciseFeatureHit(mp, [104.5, 30.5], project, P, 6).hit).toBe(true);
  });
});
