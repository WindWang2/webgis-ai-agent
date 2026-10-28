/**
 * scene3d 3D Tiles 1.1 subset + SSE 调度器测试（G3D02 M7）。
 */
import { describe, expect, it } from 'vitest';

import { buildTilesetFixture } from './__fixtures__/glb';
import { parseTileset, TilesetParseError, type TileNode } from './tiles3d/tileset';
import { scheduleTiles, type SchedulerCamera } from './tiles3d/scheduler';

const RESOLVE = (base: string, uri: string) => new URL(uri, base).toString();
const OPTS = {
  tilesetUrl: 'https://assets.test/tileset.json',
  anchor: { lng: 104.06, lat: 30.67 },
  resolveUri: RESOLVE,
};

function cam(over: Partial<SchedulerCamera> = {}): SchedulerCamera {
  return {
    eyeEnu: { x: 0, y: 0, z: 300 },
    frustumPlanes: null,
    viewportHeightPx: 800,
    fovRad: 0.6435,
    ...over,
  };
}

describe('parseTileset', () => {
  it('parses the fixture with region root + box children', () => {
    const ts = parseTileset(buildTilesetFixture(), OPTS);
    expect(ts.assetVersion).toBe('1.1');
    expect(ts.tileCount).toBe(3);
    expect(ts.root.children).toHaveLength(2);
    expect(ts.root.contentUri).toBe('https://assets.test/root.glb');
    expect(ts.root.refine).toBe('REPLACE');
    // region [104.05,30.66,104.07,30.68] 中心 ≈ 锚点 → ENU 球心近原点
    expect(ts.root.sphereEnu.cx).toBeCloseTo(0, -2);
    expect(ts.root.sphereEnu.cy).toBeCloseTo(0, -2);
    expect(ts.root.sphereEnu.radiusM).toBeGreaterThan(1000);
  });

  it('resolves relative content URIs against the tileset URL', () => {
    const doc = buildTilesetFixture();
    const ts = parseTileset(doc, { ...OPTS, tilesetUrl: 'https://cdn.example.org/a/b/tileset.json' });
    expect(ts.root.contentUri).toBe('https://cdn.example.org/a/b/root.glb');
  });

  it('rejects policy-violating content URIs (resolveUri null)', () => {
    const ts0 = buildTilesetFixture() as { root: { content: { uri: string } } };
    void ts0;
    expect(() =>
      parseTileset(buildTilesetFixture(), { ...OPTS, resolveUri: () => null }),
    ).toThrowError(TilesetParseError);
  });

  it('rejects legacy b3dm content', () => {
    const doc = buildTilesetFixture({ contentExt: 'b3dm' });
    expect(() => parseTileset(doc, OPTS)).toThrowError(/b3dm/);
  });

  it('rejects external tileset json content', () => {
    const doc = buildTilesetFixture({ contentExt: 'json' });
    expect(() => parseTileset(doc, OPTS)).toThrowError(/external tileset/);
  });

  it('rejects 1.0 content.url', () => {
    const doc = buildTilesetFixture() as unknown as {
      root: { content?: Record<string, unknown> };
    };
    doc.root.content = { url: 'root.b3dm' };
    expect(() => parseTileset(doc, OPTS)).toThrowError(/content\.url/);
  });

  it('rejects bad asset version', () => {
    const doc = buildTilesetFixture() as { asset: { version: string } };
    doc.asset.version = '0.9';
    expect(() => parseTileset(doc, OPTS)).toThrowError(TilesetParseError);
  });

  it('rejects extensionsRequired', () => {
    const doc = { ...buildTilesetFixture(), extensionsRequired: ['3DTILES_implicit_tiling'] } as unknown;
    expect(() => parseTileset(doc, OPTS)).toThrowError(/implicit_tiling/);
  });

  it('rejects invalid refine value (no silent REPLACE coercion)', () => {
    // G3DV2-B G6：曾静默 coerce REPLACE —— ADD/REPLACE 写错的数据集得到
    // 错误渲染语义；现 typed 拒绝。
    const doc = buildTilesetFixture({ invalidRootRefine: 'MIXED' });
    expect(() => parseTileset(doc, OPTS)).toThrowError(/refine 'MIXED' invalid/);
  });

  it('child refine inherits from parent when unspecified (OGC semantics)', () => {
    const ts = parseTileset(buildTilesetFixture({ refine: 'ADD', omitChildRefine: true }), OPTS);
    expect(ts.root.refine).toBe('ADD');
    expect(ts.root.children[0].refine).toBe('ADD'); // 未声明 → 继承根 ADD
    expect(ts.root.children[1].refine).toBe('REPLACE'); // 显式声明优先
  });
});

function fixtureNodes(): TileNode {
  return parseTileset(buildTilesetFixture(), OPTS).root;
}

describe('scheduleTiles', () => {
  it('far camera selects the root tile only', () => {
    const root = fixtureNodes();
    const decision = scheduleTiles(
      root,
      cam({ eyeEnu: { x: 0, y: 0, z: 100_000 } }),
      { maxSse: 16, maxVisibleTiles: 16 },
      new Set(),
      new Set(),
    );
    expect([...decision.render]).toEqual(['0']);
    expect(decision.load).toEqual(['0']);
    expect(decision.degraded.frustumCulled).toBe(0);
  });

  it('near camera refines into children (REPLACE；子孙就绪后根退出)', () => {
    const root = fixtureNodes();
    const decision = scheduleTiles(
      root,
      cam({ eyeEnu: { x: 0, y: 0, z: 150 } }),
      { maxSse: 16, maxVisibleTiles: 16 },
      new Set(['0/0', '0/1']), // 子孙已就绪
      new Set(),
    );
    expect(decision.render.has('0/0')).toBe(true);
    expect(decision.render.has('0/1')).toBe(true);
    expect(decision.render.has('0')).toBe(false); // REPLACE：子孙就绪后根退出
  });

  it('REPLACE ancestor renders while children not yet loaded (progressive)', () => {
    const root = fixtureNodes();
    const near = cam({ eyeEnu: { x: 0, y: 0, z: 150 } });
    // 全未加载：根兜底 + 子请求加载
    const d0 = scheduleTiles(root, near, { maxSse: 16, maxVisibleTiles: 16 }, new Set(), new Set());
    expect(d0.render.has('0')).toBe(true);
    expect(d0.load).toContain('0/0');
    // 子加载完成后：根退出、子渲染
    const loaded = new Set(['0/0', '0/1']);
    const d1 = scheduleTiles(root, near, { maxSse: 16, maxVisibleTiles: 16 }, loaded, d0.render);
    expect(d1.render.has('0')).toBe(false);
    expect(d1.render.has('0/0')).toBe(true);
    expect(d1.unload).toContain('0');
  });

  it('frustum culling skips off-screen subtrees without loading', () => {
    const root = fixtureNodes();
    // 视锥 = x ≥ 0 半空间（左平面 x=0 外法线 −x…构造：法线 (−1,0,0), d=0 →
    // 视锥内条件 n·p + d ≥ 0 → −x ≥ 0 → x ≤ 0）。把 child-b (x=200) 排除。
    const planes = [{ normal: { x: -1, y: 0, z: 0 }, d: 0 }];
    const decision = scheduleTiles(
      root,
      cam({ eyeEnu: { x: 0, y: 0, z: 150 }, frustumPlanes: planes }),
      { maxSse: 16, maxVisibleTiles: 16 },
      new Set(['0/0']),
      new Set(),
    );
    expect(decision.render.has('0/1')).toBe(false);
    expect(decision.load).not.toContain('0/1');
    expect(decision.degraded.frustumCulled).toBeGreaterThan(0);
  });

  it('visible-tile budget caps refinement honestly', () => {
    const root = fixtureNodes();
    const decision = scheduleTiles(
      root,
      cam({ eyeEnu: { x: 0, y: 0, z: 150 } }),
      { maxSse: 16, maxVisibleTiles: 1 },
      new Set(),
      new Set(),
    );
    expect(decision.render.size).toBeLessThanOrEqual(1);
    expect(decision.degraded.budgetCappedTiles).toBeGreaterThan(0);
  });

  it('unload = prevRender minus current render', () => {
    const root = fixtureNodes();
    const near = cam({ eyeEnu: { x: 0, y: 0, z: 150 } });
    const loaded = new Set(['0/0', '0/1']);
    const d1 = scheduleTiles(root, near, { maxSse: 16, maxVisibleTiles: 16 }, loaded, new Set(['0']));
    expect(d1.unload).toContain('0');
    const far = cam({ eyeEnu: { x: 0, y: 0, z: 100_000 } });
    const d2 = scheduleTiles(root, far, { maxSse: 16, maxVisibleTiles: 16 }, loaded, d1.render);
    expect(d2.render.has('0')).toBe(true);
    expect(d2.unload).toEqual(expect.arrayContaining(['0/0', '0/1']));
  });
});
