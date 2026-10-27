import { describe, it, expect, beforeEach } from 'vitest';
import { MapSpecRuntime } from './runtime';
import {
  _resetSceneEvidenceForTests,
  sceneEvidenceSnapshot,
} from './adapter';

/**
 * G3D01 D8：底图 raster 源瓦片失败 → `scene_basemap_unavailable` 证据。
 * 断网/401/403/429 不再只是 MapLibre 控制台错误（静默降级），证据进入
 * observation/导出披露面。与 scene_terrain_unavailable（ADR-0199 P1-3）
 * 同一 handler 通道。
 */
function errorCapableMockMap() {
  const handlers = new Map<string, Set<(e?: unknown) => void>>();
  const map: any = {
    on(event: string, handler: (e?: unknown) => void) {
      const set = handlers.get(event) ?? new Set();
      set.add(handler);
      handlers.set(event, set);
    },
    off(event: string, handler: (e?: unknown) => void) {
      handlers.get(event)?.delete(handler);
    },
    isStyleLoaded: () => true,
    getStyle: () => ({ sources: {}, layers: [] }),
    getSource: () => undefined,
    getLayer: () => undefined,
    _emitError(e: unknown) {
      for (const handler of Array.from(handlers.get('error') ?? [])) handler(e);
    },
  };
  return map;
}

describe('basemap 瓦片失败证据（G3D01 D8）', () => {
  beforeEach(() => {
    _resetSceneEvidenceForTests();
  });

  it('raster-tiles-{index} 源错误 → scene_basemap_unavailable', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map);
    map._emitError({ sourceId: 'raster-tiles-0' });
    const codes = sceneEvidenceSnapshot().map((e) => e.code);
    expect(codes).toContain('scene_basemap_unavailable');
    void rt;
  });

  it('非底图源错误不误报', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map);
    map._emitError({ sourceId: 'some-user-layer' });
    expect(sceneEvidenceSnapshot()).toEqual([]);
    void rt;
  });

  it('地形源错误优先归 scene_terrain_unavailable（不串道）', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map);
    // appliedSpec 未设置时 terrain 分支不命中；直接模拟已绑定 terrain 的
    // 错误源 id 形态不能既是 raster-tiles-* 又是 terrain.source —— 若
    // appliedSpec 声明了 terrain.source = 'raster-tiles-0'（病态 spec），
    // terrain 分支必须先行短路。
    (rt as any).appliedSpec = {
      scene: { terrain: { source: 'raster-tiles-0' } },
    };
    map._emitError({ sourceId: 'raster-tiles-0' });
    const entries = sceneEvidenceSnapshot();
    expect(entries.some((e) => e.code === 'scene_terrain_unavailable')).toBe(true);
    expect(entries.some((e) => e.code === 'scene_basemap_unavailable')).toBe(false);
  });

  it('无 sourceId 的错误安全忽略', () => {
    const map = errorCapableMockMap();
    const rt = new MapSpecRuntime(map);
    map._emitError({ message: 'style load failed' });
    map._emitError(null);
    expect(sceneEvidenceSnapshot()).toEqual([]);
    void rt;
  });
});
