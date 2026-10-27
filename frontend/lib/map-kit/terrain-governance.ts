import type { Map } from 'maplibre-gl';
import { DEPLOYMENT_PROFILE } from '@/lib/providers';
import { recordSceneEvidence } from '@/lib/mapspec-runtime/adapter';
import { disable3DTerrain, enable3DTerrain, type TerrainOptions } from './renderer';

/**
 * 3D 地形挂载的唯一治理入口（G3D01 D6 / review R1 P1-1）。
 *
 * 此前 map-panel 有两个挂载点（is3D toggle effect 与底图切换/style 落定
 * 后的重挂），后者曾无条件 enable3DTerrain —— air_gapped 部署下一次底图
 * 切换就会静默重新挂上外网 DEM 兜底，绕过 toggle 侧守卫。两个调用点必须
 * 共用本函数：air_gapped 且 spec 未绑定会话 DEM 源（无 url）时显式禁用
 * 并披露证据，绝不静默回退（cloud 维持治理画像兜底行为）。
 */
export function applyTerrain3D(map: Map, options: TerrainOptions = {}): void {
  if (!options.url && DEPLOYMENT_PROFILE === 'air_gapped') {
    disable3DTerrain(map);
    recordSceneEvidence('scene_terrain_no_source_air_gapped', 'terrain');
    return;
  }
  enable3DTerrain(map, options);
}
