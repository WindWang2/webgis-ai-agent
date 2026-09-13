/**
 * legend-labels —— legend_spec v2 消费面（AC-07 / ADR-0156 §P7）。
 *
 * 03 线冻结的 legend_spec v2 增量字段（`unit` / `nodata_label` /
 * `out_of_range_label` / `method` / `k`）在共享类型（map-kit/types.ts）
 * 尚未收录 —— 本模块用局部窄类型读取（additive、legacy payload 干净缺省），
 * 不改共享契约文件；03 合入后由类型通道自然接管。
 *
 * 统一规则：
 * - 单位：图例卡（categorical/graduated）与色条（continuous）统一以
 *   「unit」尾注呈现，缺省不占位；
 * - nodata：标签优先级 nodata_label > nodata.label > 「无数据」（v1 语义）；
 * - out_of_range：标签优先 out_of_range_label > 「超出分级范围」（v1 无此
 *   概念 → 缺省不渲染条目，字段在场才渲染）；
 * - k：类目数披露（k > entries 时给出「…N 类未显示」的诚实口径）。
 */

import type { LegendSpec } from '@/lib/map-kit/types';

/** v2 增量字段（局部窄类型 —— additive 读取）。 */
interface LegendSpecV2Fields {
  unit?: string;
  nodata_label?: string;
  out_of_range_label?: string;
  method?: string;
  k?: number;
  nodata?: { color?: string; label?: string };
}

type LegendSpecMaybeV2 = LegendSpec & LegendSpecV2Fields;

export const DEFAULT_NODATA_LABEL = '无数据';
export const DEFAULT_OUT_OF_RANGE_LABEL = '超出分级范围';

/** 单位尾注（如「单位：万 m²」；unit 缺省 → ''）。 */
export function legendUnitSuffix(legend: LegendSpec | undefined): string {
  const unit = (legend as LegendSpecMaybeV2 | undefined)?.unit;
  return typeof unit === 'string' && unit ? `单位：${unit}` : '';
}

/** nodata 条目标签（优先级：nodata_label > nodata.label > 「无数据」）。 */
export function legendNodataLabel(legend: LegendSpec | undefined): string {
  const l = legend as LegendSpecMaybeV2 | undefined;
  if (!l) return DEFAULT_NODATA_LABEL;
  if (typeof l.nodata_label === 'string' && l.nodata_label) return l.nodata_label;
  const nodata = l.nodata;
  if (nodata && typeof nodata.label === 'string' && nodata.label) return nodata.label;
  return DEFAULT_NODATA_LABEL;
}

/** out_of_range 标签（仅 v2 字段在场才返回非空 —— v1 不渲染该条目）。 */
export function legendOutOfRangeLabel(legend: LegendSpec | undefined): string {
  const label = (legend as LegendSpecMaybeV2 | undefined)?.out_of_range_label;
  return typeof label === 'string' && label ? label : '';
}

/** 类目数 k（v2 显式 k > breaks 推断 > categories 数）。 */
export function legendClassCount(legend: LegendSpec | undefined): number {
  const l = legend as LegendSpecMaybeV2 | undefined;
  if (!l) return 0;
  if (typeof l.k === 'number' && l.k > 0) return l.k;
  if ('breaks' in l && Array.isArray(l.breaks) && l.breaks.length > 1) {
    return l.breaks.length - 1;
  }
  if ('categories' in l && Array.isArray(l.categories)) return l.categories.length;
  return 0;
}

/** 分类方法标签（method 存在时以小字披露于图例标题旁）。 */
export function legendMethodLabel(legend: LegendSpec | undefined): string {
  const method = (legend as LegendSpecMaybeV2 | undefined)?.method;
  return typeof method === 'string' && method ? method : '';
}
