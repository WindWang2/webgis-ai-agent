import { describe, expect, it } from 'vitest';
import { TILE_PROVIDERS } from './providers';

/**
 * #536：OpenTopoMap 的 `{s}` 子域占位符（Leaflet 时代写法）MapLibre 不展开。
 * maplibre-gl@5.24.0 的 CanonicalTileID.url() 替换链只处理
 * {prefix}/{z}/{x}/{y}/{ratio}/{quadkey}/{bbox-epsg-3857}；`{s}` 会原样进入
 * tile 请求的 hostname → DNS 失败 → 底图空白且无可见错误（style 加载成功、
 * 唯一 ack 的步骤通过）。本测试对全表做占位符白名单扫描 + 回归锚点。
 */
const MAPLIBRE_TOKEN_WHITELIST = new Set([
  '{prefix}',
  '{z}',
  '{x}',
  '{y}',
  '{ratio}',
  '{quadkey}',
  '{bbox-epsg-3857}',
]);

describe('TILE_PROVIDERS URL 占位符契约（#536）', () => {
  it('每个 provider URL 都没有 MapLibre 不认识的 {…} 占位符', () => {
    expect(TILE_PROVIDERS.length).toBeGreaterThan(0);
    const offenders: string[] = [];
    for (const provider of TILE_PROVIDERS) {
      const tokens = provider.url.match(/\{[^}]*\}/g) ?? [];
      for (const token of tokens) {
        if (!MAPLIBRE_TOKEN_WHITELIST.has(token)) {
          offenders.push(`${provider.id}: ${provider.url} 含未支持占位符 ${token}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('opentopomap 的 {s} 已展开为具体子域（回归锚点）', () => {
    const otm = TILE_PROVIDERS.find((p) => p.id === 'opentopomap');
    expect(otm).toBeTruthy();
    expect(otm!.url).toBe('https://a.tile.opentopomap.org/{z}/{x}/{y}.png');
    expect(otm!.url).not.toContain('{s}');
    // hostname 不再含占位符 —— 展开后请求可解析
    const hostname = otm!.url.replace(/\{z\}\/\{x\}\/\{y\}\.png$/, '');
    expect(/^https:\/\/[a-z0-9.-]+\/$/.test(hostname)).toBe(true);
  });

  it('渲染路径不自行展开占位符（map-panel 原样透传）→ hostname 必须无占位符', () => {
    // map-panel.getMapStyle 把 raster provider.url 原样放进 tiles:[url]；
    // 若 hostname 里有未支持 token（如 {s}.tile…），MapLibre 不展开 → DNS
    // 失败 → 空白底图，且渲染路径无处补救。查询串里的 {z}/{x}/{y} 是合法
    // 瓦片参数，只校验 hostname。
    for (const provider of TILE_PROVIDERS) {
      if (provider.type !== 'raster') continue;
      const hostname = new URL(provider.url).hostname;
      expect(hostname, `${provider.id} hostname 含占位符`).not.toMatch(/[{}]/);
    }
  });
});