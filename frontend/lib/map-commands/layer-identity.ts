import * as renderer from '@/lib/map-kit/renderer';
import { getCommittedMapSpec } from '@/lib/mapspec/session-cursor';

/**
 * LayerIdentityResolver —— 图层身份解析的单一深接口（Goal D / #10.2）。
 *
 * 一个逻辑图层在不同层各有 id 形态：
 *   数据 ref（ref:geojson-*）↔ HUD store 行 id ↔ MapSpec spec 层 id ↔
 *   MapLibre 渲染子层（store 方案 `${id}__*` / 自定义方案 `custom-${id}*`）。
 * 此前每条命令各写一遍 prefix/fallback 匹配；现在 show/hide/opacity/
 * remove/reorder/finalize 全部经此模块解析，不再有第二套实现。
 *
 * 解析链（命中即止，一层→多层是显式 group 语义）：
 *   1. store 层 id 直接命中（在飞会话常态）；
 *   2. store 层 `_refId` 命中（挂载路径的 HUD 层）；
 *   3. committed MapSpec：ref → source（ref_id 字段）→ 引用该 source 的
 *      全部 spec 层 id（一个 ref 可背多层，如 product-heatmap + product-points）。
 */

export interface HudLayerLike {
  id: string;
  _refId?: string;
  _mapspecLayerId?: string;
  [key: string]: unknown;
}

export interface HudStateLike {
  layers?: HudLayerLike[];
  updateLayer?: (id: string, patch: Record<string, unknown>) => void;
  removeLayer?: (id: string) => void;
}

/** MapLibre 子层 id 方案匹配（store `${id}__*` 与 custom `custom-${id}*`）。 */
export function isCustomSchemeMatch(target: string, id: string): boolean {
  const custom = `custom-${target}`;
  return id === custom || id.startsWith(`${custom}-`) || id.startsWith(`${custom}_`);
}

export function isStoreSchemeMatch(target: string, id: string): boolean {
  return id === target || id.startsWith(`${target}__`);
}

/** 命中目标的所有 MapLibre 图层 id（双方案；#462 registry 读，无 style 深拷贝）。 */
export function matchMapLayers(map: unknown, target: string): string[] {
  return renderer
    .getStyleLayerIds(map)
    .filter((id: string) => isCustomSchemeMatch(target, id) || isStoreSchemeMatch(target, id));
}

/**
 * 跨 id 体系的图层目标解析：ref / store id / spec id → 全部 spec 层目标。
 * 一个 ref 可背多层（同源 heatmap + points）——全部返回（group 语义）。
 */
export function resolveLayerTargetsByRef(
  layerId: string,
  getHudState: () => HudStateLike,
): string[] {
  const layers: HudLayerLike[] = getHudState().layers ?? [];
  if (layers.some((l) => l.id === layerId)) return [layerId];
  const byRefId = layers.filter((l) => l._refId === layerId).map((l) => l.id);
  if (byRefId.length > 0) return byRefId;
  const spec = getCommittedMapSpec();
  if (!spec?.sources || !spec?.layers) return [];
  const sourceIds = new Set(
    Object.entries(spec.sources as Record<string, { ref_id?: string; ref?: string }>)
      .filter(([, s]) => (s?.ref_id ?? s?.ref) === layerId)
      .map(([id]) => id),
  );
  if (sourceIds.size === 0) return [];
  // 不要求 store 行：product-* 等后端直写图层可能尚无 HUD 行（镜像
  // syncSpecLayersToStore 与本解析互为兜底）。无行的目标由调用侧经
  // mergePendingPresentation 落到 spec 层——绝不能因面板无行而漏隐藏。
  return (spec.layers as { id: string; source?: string }[])
    .filter((l) => sourceIds.has(String(l.source || '')))
    .map((l) => String(l.id));
}

/** 目标对应的 MapSpec spec 层 id（store 行 `_mapspecLayerId` 优先，兜底自身 id）。 */
export function specLayerIdOf(layer: HudLayerLike | undefined, fallbackId: string): string {
  return String((layer as { _mapspecLayerId?: string } | undefined)?._mapspecLayerId ?? fallbackId);
}

export interface ResolvedLayerTargets {
  /** 逻辑目标（store id / spec id）——store 更新与 pending presentation 的键。 */
  targetIds: string[];
  /** 命中的 MapLibre 图层 id（可能为空：runtime 尚未 reconcile）。 */
  matchedMapIds: string[];
  /** targetIds 中属于 store 方案子层的 MapLibre id（异步 reconcile 所有）。 */
  storeMatchedMapIds: string[];
}
