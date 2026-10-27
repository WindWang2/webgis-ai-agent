import { describe, expect, it } from 'vitest';
import { getMapStyle } from './map-panel';
import { TILE_PROVIDERS } from '@/lib/providers';

/**
 * G3D01 D7（review R2 P2-3）：per-provider 署名必须真实流进合成的 raster
 * source —— 此前 map-panel 对所有 raster provider 硬编码 OSM 署名（对
 * ESRI/Carto/OpenTopoMap/高德是错误署名，许可红线）。回退即红。
 */
describe('getMapStyle per-provider attribution（D7 许可红线）', () => {
  it('raster provider 携带自身署名（ESRI ≠ OpenStreetMap）', () => {
    const style = getMapStyle(
      {
        name: 'ESRI 影像',
        url: 'https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        type: 'raster',
        attribution: 'Esri, Maxar, Earthstar Geographics',
      },
      0,
    ) as { sources: Record<string, { attribution?: string }> };
    expect(style.sources['raster-tiles-0'].attribution).toBe(
      'Esri, Maxar, Earthstar Geographics',
    );
  });

  it('OpenFreeMap 矢量样式原样透传 style.json URL（署名由样式内的 source 承载）', () => {
    const url = getMapStyle(
      {
        name: 'OpenFreeMap Positron',
        url: 'https://openfreemap.org/style/positron',
        type: 'vector',
        attribution: '© OpenFreeMap © OpenMapTiles Data from OpenStreetMap',
      },
      1,
    );
    expect(url).toBe('https://openfreemap.org/style/positron');
  });

  it('治理目录全量 raster 条目署名非空且逐条随 provider（漂移即红）', () => {
    for (const p of TILE_PROVIDERS) {
      if (p.type !== 'raster') continue;
      const style = getMapStyle(
        { name: p.name, url: p.url, type: p.type, attribution: p.attribution },
        2,
      ) as { sources: Record<string, { attribution?: string }> };
      const got = style.sources['raster-tiles-2'].attribution;
      expect(got, `${p.id} 署名必须随 provider`).toBe(p.attribution);
      expect(got, `${p.id} 署名非空`).not.toBe('');
    }
  });
});
