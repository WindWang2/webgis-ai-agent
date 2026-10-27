import { describe, expect, it, vi } from 'vitest';
import {
  BASEMAP_PROVIDER_PROFILES,
  ELEVATION_FALLBACK_ID,
  ELEVATION_SOURCE_PROFILES,
  type BasemapProviderProfile,
} from './basemap-profiles.generated';

/**
 * G3D01 双侧契约锁（前端侧）：TILE_PROVIDERS（渲染端真相）必须与
 * basemap-profiles.generated.ts（后端 typed 治理目录的投影）逐条一致。
 * 名字/署名/许可/URL 漂移在此即红 —— 手工双真相时代终结。
 * （Python 侧幂等锁：tests/cartography/test_basemap_profile_projection.py）
 */

const TEST_ENV: Record<string, string> = {
  NEXT_PUBLIC_TIANDITU_TOKEN: 'TEST-TOKEN',
  NEXT_PUBLIC_LOCAL_BASEMAP_URL: 'http://tiles.intranet/{z}/{x}/{y}.png',
};

async function loadProvidersWithEnv(env: Record<string, string | undefined>) {
  vi.resetModules();
  vi.unstubAllEnvs();
  for (const k of Object.keys(TEST_ENV)) delete process.env[k];
  for (const [k, v] of Object.entries(env)) {
    if (v !== undefined) vi.stubEnv(k, v);
  }
  return await import('./providers');
}

/** profile.url 的 {env:VAR} 占位 → 用同一 env 注入值展开（与 providers.ts 构建对齐）。 */
function resolveProfileUrl(profile: BasemapProviderProfile, env: Record<string, string>): string {
  return profile.url.replace(/\{env:([A-Z0-9_]+)\}/g, (_, name: string) => env[name] ?? '');
}

describe('TILE_PROVIDERS ≡ 治理画像契约（G3D01）', () => {
  it('env 门全开时：两侧 id 集合完全一致（无幽灵 provider、无遗漏）', async () => {
    const mod = await loadProvidersWithEnv({ ...TEST_ENV });
    const frontendIds = mod.TILE_PROVIDERS.map((p) => p.id).sort();
    const profileIds = BASEMAP_PROVIDER_PROFILES.map((p) => p.providerId).sort();
    expect(frontendIds).toEqual(profileIds);
  });

  it('逐条一致：name / attribution / type / keywords / url', async () => {
    const mod = await loadProvidersWithEnv({ ...TEST_ENV });
    for (const profile of BASEMAP_PROVIDER_PROFILES) {
      const provider = mod.TILE_PROVIDERS.find((p) => p.id === profile.providerId);
      expect(provider, `前端缺少治理画像条目 ${profile.providerId}`).toBeTruthy();
      expect(provider!.name, `${profile.providerId}.name`).toBe(profile.name);
      expect(provider!.attribution, `${profile.providerId}.attribution`).toBe(profile.attribution);
      expect(provider!.type, `${profile.providerId}.type`).toBe(profile.mapStyleType);
      expect(provider!.keywords, `${profile.providerId}.keywords`).toEqual([...profile.keywords]);
      expect(provider!.url, `${profile.providerId}.url`).toBe(
        resolveProfileUrl(profile, TEST_ENV),
      );
    }
  });

  it('每个 provider 都携带非空署名（数据诚信：无署名的源不可用）', () => {
    for (const profile of BASEMAP_PROVIDER_PROFILES) {
      expect(profile.attribution.trim(), profile.providerId).not.toBe('');
      expect(profile.license.trim(), profile.providerId).not.toBe('');
    }
  });

  it('tianditu 缺 token：前端诚实缺席，但画像仍在目录（LLM 侧如实感知）', async () => {
    const mod = await loadProvidersWithEnv({
      NEXT_PUBLIC_TIANDITU_TOKEN: undefined,
      NEXT_PUBLIC_LOCAL_BASEMAP_URL: undefined,
    });
    expect(mod.TILE_PROVIDERS.some((p) => p.id === 'tianditu-vec')).toBe(false);
    expect(BASEMAP_PROVIDER_PROFILES.some((p) => p.providerId === 'tianditu-vec')).toBe(true);
  });

  it('OpenFreeMap 三个开源矢量样式在两侧注册且免凭据', async () => {
    const mod = await loadProvidersWithEnv({ ...TEST_ENV });
    const ofmIds = ['openfreemap-libre', 'openfreemap-positron', 'openfreemap-bright'];
    for (const id of ofmIds) {
      const profile = BASEMAP_PROVIDER_PROFILES.find((p) => p.providerId === id);
      expect(profile, id).toBeTruthy();
      expect(profile!.requiresCredentials).toBe(false);
      const provider = mod.TILE_PROVIDERS.find((p) => p.id === id);
      expect(provider, id).toBeTruthy();
      expect(provider!.type).toBe('vector');
    }
  });
});

describe('高程源画像契约（G3D01 D5/D6）', () => {
  it('兜底源 aws-terrarium-public：terrarium 编码、display_only、https', () => {
    const fb = ELEVATION_SOURCE_PROFILES.find((p) => p.sourceId === ELEVATION_FALLBACK_ID);
    expect(fb).toBeTruthy();
    expect(fb!.encoding).toBe('terrarium');
    expect(fb!.qualification).toBe('display_only');
    expect(fb!.url!.startsWith('https://')).toBe(true);
    // 混合源垂直基准不统一 → 必须显式为 null（不可伪造基准）
    expect(fb!.verticalDatum).toBeNull();
  });

  it('合成 DEM 画像：synthetic=true 强制 display_only', () => {
    const syn = ELEVATION_SOURCE_PROFILES.find((p) => p.sourceId === 'synthetic-demo-dem');
    expect(syn).toBeTruthy();
    expect(syn!.synthetic).toBe(true);
    expect(syn!.qualification).toBe('display_only');
  });

  it('remote 高程源一律 https（防降级/内网泄漏）', () => {
    for (const e of ELEVATION_SOURCE_PROFILES) {
      if (e.kind === 'remote_terrarium_xyz') {
        expect(e.url!.startsWith('https://'), e.sourceId).toBe(true);
      }
    }
  });
});
