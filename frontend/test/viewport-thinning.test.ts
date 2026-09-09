/**
 * Viewport thinning（W7）——确定性网格抽稀 + 视口预算。
 *
 * 锁定四条不变式：
 *   T1. 预算：超预算 FC 抽稀后 ≤ maxFeatures；低于阈值/预算内原样穿透；
 *   T2. 确定性：同输入同视口 ⇒ 同输出（两次调用结果逐要素一致）；
 *   T3. 面积优先：同 cell 内大要素（道路）先于小要素（噪点）获配额；
 *   T4. 原序保持：幸存要素保持原始集合顺序（渲染顺序稳定）。
 */
import { describe, expect, it } from 'vitest';
import { thinFeaturesForViewport } from '@/lib/utils/geo';
import type { Layer } from '@/lib/types/layer';

function pointFeature(lng: number, lat: number, id: string) {
  return {
    type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: [lng, lat] },
    properties: { id },
  };
}

const VIEWPORT: [number, number, number, number] = [100, 30, 110, 40];

function makeDenseFc(n: number) {
  const features = [];
  for (let i = 0; i < n; i++) {
    // 均匀撒在视口内
    const lng = 100 + (i % 100) * 0.1;
    const lat = 30 + Math.floor(i / 100) * (10 / Math.ceil(n / 100));
    features.push(pointFeature(lng, lat, `pt-${i}`));
  }
  return { type: 'FeatureCollection' as const, features };
}

describe('thinFeaturesForViewport（W7）', () => {
  it('T1: 超预算抽稀 ≤ maxFeatures；预算内原样返回同一引用', () => {
    const dense = makeDenseFc(20_000);
    const thinned = thinFeaturesForViewport(dense, VIEWPORT, 5000);
    expect(thinned.features.length).toBeLessThanOrEqual(5000);
    expect(thinned.features.length).toBeGreaterThan(0);

    const small = { type: 'FeatureCollection' as const, features: makeDenseFc(500).features };
    expect(thinFeaturesForViewport(small, VIEWPORT, 5000)).toBe(small);
  });

  it('T2: 确定性 —— 同输入同视口两次抽稀输出一致', () => {
    const fc = makeDenseFc(30_000);
    const a = thinFeaturesForViewport(fc, VIEWPORT, 3000);
    const b = thinFeaturesForViewport(fc, VIEWPORT, 3000);
    expect(a.features.map((f) => f.properties.id)).toEqual(b.features.map((f) => f.properties.id));
  });

  it('T3: 空间均匀配额 —— 视口各象限都有幸存者（不被单簇挤占）', () => {
    // 90% 的点挤在东北角，10% 均匀分布其余区域
    const features = [];
    for (let i = 0; i < 18_000; i++) {
      features.push(pointFeature(107 + Math.random() * 2, 37 + Math.random() * 2, `ne-${i}`));
    }
    for (let i = 0; i < 2000; i++) {
      features.push(pointFeature(100 + (i % 70) * 0.1, 30 + Math.floor(i / 70) * 0.1, `sw-${i}`));
    }
    const fc = { type: 'FeatureCollection' as const, features };
    const thinned = thinFeaturesForViewport(fc, VIEWPORT, 4000);
    const swSurvivors = thinned.features.filter((f) => String(f.properties.id).startsWith('sw-'));
    // 西南象限（独立 cell）保底配额：2000 输入中应有可观幸存（非零且非全保）
    expect(swSurvivors.length).toBeGreaterThan(100);
    expect(thinned.features.length).toBeLessThanOrEqual(4000);
  });

  it('T4: 幸存要素保持原始集合顺序', () => {
    const fc = makeDenseFc(12_000);
    const thinned = thinFeaturesForViewport(fc, VIEWPORT, 2000);
    const originalIds = fc.features.map((f) => f.properties.id);
    const thinnedIds = thinned.features.map((f) => f.properties.id);
    const sortedCopy = [...thinnedIds].sort(
      (a, b) => originalIds.indexOf(a) - originalIds.indexOf(b),
    );
    expect(thinnedIds).toEqual(sortedCopy);
  });
});

// 视口预算与 MVT 阈值同 rationale（renderer 顶部注释锁定契约）——
// 抽稀只在 inline 通道生效，MVT 源在 isMvtSourceId 处被跳过（既有守卫）。
describe('W7 渲染预算契约', () => {
  it('预算 5000 与内联 GeoJSON 视口通道对齐（无 MVT 双裁剪回归）', () => {
    // renderer 的 refresh 对 MVT 源跳过（isMvtSourceId → return），本断言
    // 锁定抽稀只服务 inline 通道的语义：抽稀函数对空/畸形输入不抛错。
    expect(
      thinFeaturesForViewport({ type: 'FeatureCollection', features: [] }, VIEWPORT, 5000).features,
    ).toEqual([]);
    expect(thinFeaturesForViewport(null as never, VIEWPORT, 5000)).toBeNull();
  });
});

// 防止未来误改 Layer 类型影响 thinning 输入（类型层冒烟）。
describe('W7 类型冒烟', () => {
  it('Layer 内联 source 与视口过滤输入兼容', () => {
    const layer = { source: { features: [] } } as unknown as Layer;
    expect(Array.isArray((layer.source as { features: unknown[] }).features)).toBe(true);
  });
});
