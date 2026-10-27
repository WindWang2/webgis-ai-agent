import { describe, expect, it, vi } from 'vitest';

/**
 * G3D01 review R1 P1-1 回归：3D 地形挂载的唯一治理入口。
 * 此前 map-panel 的底图切换重挂点无条件 enable3DTerrain —— air_gapped
 * 部署一次底图切换即静默重挂外网 DEM 兜底。两个挂载点必须共用
 * applyTerrain3D；本测试直接锁定守卫语义（map-panel 侧由既有
 * map-basemap-lifecycle 套件覆盖重挂行为）。
 *
 * 注意：证据环是 adapter 模块级单例 —— loadWithEnv 会 resetModules，
 * 故 snapshot/reset 必须与被测模块**同批**动态 import（顶层静态 import
 * 会拿到另一个实例，断言恒空）。
 */

async function loadWithEnv(env: Record<string, string | undefined>) {
  vi.resetModules();
  vi.unstubAllEnvs();
  delete process.env.NEXT_PUBLIC_DEPLOYMENT_PROFILE;
  for (const [k, v] of Object.entries(env)) {
    if (v !== undefined) vi.stubEnv(k, v);
  }
  const [governance, adapter] = await Promise.all([
    import('./terrain-governance'),
    import('@/lib/mapspec-runtime/adapter'),
  ]);
  adapter._resetSceneEvidenceForTests();
  return {
    governance,
    evidence: () => adapter.sceneEvidenceSnapshot().map((e) => e.code),
  };
}

function mockMap() {
  return {
    _added: [] as Array<{ id: string; def: any }>,
    getSource: () => undefined,
    addSource(id: string, def: any) {
      this._added.push({ id, def });
    },
    setTerrain: vi.fn(),
  } as any;
}

describe('applyTerrain3D 治理入口（R1 P1-1）', () => {
  it('air_gapped 且无会话 DEM 源：禁用地形 + 披露证据，绝不挂外网兜底', async () => {
    const { governance, evidence } = await loadWithEnv({
      NEXT_PUBLIC_DEPLOYMENT_PROFILE: 'air_gapped',
    });
    const map = mockMap();
    governance.applyTerrain3D(map, {}); // 无 url = spec 未绑定会话 DEM
    expect(map.setTerrain).toHaveBeenCalledWith(null);
    expect(map._added).toEqual([]); // 不得 addSource 外网 DEM
    expect(evidence()).toContain('scene_terrain_no_source_air_gapped');
  });

  it('air_gapped 但有会话 DEM 源：正常挂载（会话内数据不受 egress 限制）', async () => {
    const { governance, evidence } = await loadWithEnv({
      NEXT_PUBLIC_DEPLOYMENT_PROFILE: 'air_gapped',
    });
    const map = mockMap();
    governance.applyTerrain3D(map, {
      url: '/api/v1/layers/data/ref-dem/terrain-tiles/{z}/{x}/{y}.png',
    });
    expect(map._added.length).toBe(1);
    expect(map.setTerrain).toHaveBeenCalledWith(expect.objectContaining({ source: 'terrain-aws' }));
    expect(evidence()).toEqual([]);
  });

  it('cloud 无会话 DEM 源：治理画像兜底（行为与 ADR-0199 D10 一致）', async () => {
    const { governance, evidence } = await loadWithEnv({
      NEXT_PUBLIC_DEPLOYMENT_PROFILE: undefined,
    });
    const map = mockMap();
    governance.applyTerrain3D(map, {});
    expect(map._added.length).toBe(1);
    expect(map._added[0].def.tiles[0]).toContain('elevation-tiles-prod/terrarium/');
    expect(evidence()).toEqual([]);
  });
});
