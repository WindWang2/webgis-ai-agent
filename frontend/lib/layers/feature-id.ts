/**
 * feature-id — 稳定要素 id 解析（V7 属性表抽取的共享实现）。
 *
 * 与 lib/store/layer-data.ts 的 FEATURE_ID_KEYS、lib/selection/brush-select.ts
 * 的 BRUSH_ID_KEYS 保持同一解析序 —— map 框选 / 表格行 / 图表三侧的
 * 「同一要素」必须解析出同一个 id，selection-store 的 id 过滤才能双向编译。
 * 字段名约定（沿 brush-select）：properties 命中返回键名；仅 GeoJSON 顶层
 * id 在场时返回 '$id'（MapLibre ['id'] 表达式语义，properties 取不到）。
 * 返回 null = 无稳定 id（发布侧不虚构过滤）。
 */
// G14：_wfid 最优先 —— 已编辑数据集的稳定身份（后端 ADR-0277「物化后一律
// WFID 寻址」）；未物化层无该键，解析序不变。
export const FEATURE_ID_KEYS = ['_wfid', 'id', 'OBJECTID', 'fid', 'FID', 'osm_id', '@id', 'featureId', 'feature_id'] as const;

export function resolveFeatureId(
  properties: Record<string, unknown> | null | undefined,
  topLevelId?: unknown,
): string | number | null {
  if (properties) {
    for (const key of FEATURE_ID_KEYS) {
      const value = (properties as Record<string, unknown>)[key];
      if (value != null && value !== '') {
        if (typeof value === 'string' && value.startsWith('h-')) continue;
        if (typeof value === 'string' && !value.trim()) continue;
        return typeof value === 'number' ? value : String(value);
      }
    }
  }
  if (topLevelId != null && topLevelId !== '') return topLevelId as string | number;
  return null;
}

/** id 值所在的字段名（'$id' = GeoJSON 顶层 id）。 */
export function resolveFeatureIdField(
  properties: Record<string, unknown> | null | undefined,
  topLevelId?: unknown,
): string | null {
  if (properties) {
    for (const key of FEATURE_ID_KEYS) {
      const value = properties[key];
      if (value != null && value !== '') {
        if (typeof value === 'string' && value.startsWith('h-')) continue;
        if (typeof value === 'string' && !value.trim()) continue;
        return key;
      }
    }
  }
  if (topLevelId != null && topLevelId !== '') return '$id';
  return null;
}
