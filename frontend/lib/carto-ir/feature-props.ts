/**
 * 要素属性 typed 访问面（C11，issue #1556）。
 *
 * GeoJSON `properties` 在 GeoJSON 规范里就是开放 record —— 这不是债；
 * 债是消费侧 `(feature.properties as any)?.OBJECTID` 式魔法字段裸访问：
 * 字段改名/类型漂移只能在运行时黑图时暴露。本模块给出：
 *  - `FeatureProperties`：属性值域的显式类型（GeoJSON 规范：string |
 *    number | boolean | null；undefined 表缺键）；
 *  - `propOf`：带类型守卫的读取（替代 as any 链）；
 *  - `featureIdOf`：要素标识字段的单一裁决（OBJECTID 是 ESRI 导出链的
 *    既有事实缺省 —— 此前散落各处的魔法字符串收口于此）。
 *
 * 刻意不 import `geojson` 类型包：properties 的形状就是本模块关注点，
 * 本地最小形状零传递依赖（@types/geojson 是传递 hoist，非直接依赖）。
 */

/** GeoJSON 规范属性值域（+ undefined 表缺键）。 */
export type FeaturePropertyValue = string | number | boolean | null | undefined;

export type FeatureProperties = Record<string, FeaturePropertyValue>;

/** 任意 GeoJSON feature 的最小形状（properties 可缺失 —— 规范允许）。 */
export interface FeatureLike {
  properties?: FeatureProperties | null;
  [key: string]: unknown;
}

/** 字段名词表：要素标识字段的既有事实缺省（ESRI/OID 导出链）。 */
export const FEATURE_ID_FIELDS = ["OBJECTID", "id", "fid", "FID"] as const;

/** typed 读取：properties 缺失/类型不符 → undefined，绝不抛。 */
export function propOf(feature: FeatureLike | undefined | null, key: string): FeaturePropertyValue {
  const props = feature?.properties;
  if (props === null || props === undefined || typeof props !== "object") return undefined;
  const value = (props as FeatureProperties)[key];
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  return undefined;
}

/** 要素标识：id 字段（ESRI 链 OBJECTID 优先，同 #1556 既有语义），typed。 */
export function featureIdOf(feature: FeatureLike | undefined | null): FeaturePropertyValue {
  for (const key of FEATURE_ID_FIELDS) {
    const v = propOf(feature, key);
    if (v !== undefined) return v;
  }
  return undefined;
}
