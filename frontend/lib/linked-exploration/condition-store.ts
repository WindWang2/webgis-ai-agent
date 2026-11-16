/**
 * G46 多条件并存 store —— 与 SelectionStore 分离的外部 store（不替换它）。
 *
 * 语义（任务书 §三）：
 * - 本域**活动过滤**与 hover/焦点/选中不是同一概念：一个控件关闭或一次
 *   地图点击不能偷偷清掉已保存条件（E16/E17）；
 * - 一个组件操作只增删自己的条件（E04/E05：clearChart ≠ clearAll）；
 * - 地图过滤 = 组合 where 的 MapLibre 精确表达式（typed identity）；
 * - 会话世代 epoch：异步发布者跨 await 核对（E24：迟到结果不写新会话）。
 */
import {
  compileCombinedWhere,
  whereToMaplibre,
  type ChartSpec,
  type ExplorationCondition,
} from './contracts';

export interface ActiveExplorationState {
  sourceRef: string;
  sourceRevision: number;
  /** 绑定的 MapSpec 层 id（过滤投影目标；_mapspecLayerId 别名由接线层解析） */
  layerId: string;
  charts: Map<string, ChartSpec>;
  conditions: Map<string, ExplorationCondition>;
}

let state: ActiveExplorationState | null = null;
let generation = 0;
let epoch = 0;
const listeners = new Set<() => void>();

function emit(): void {
  generation += 1;
  listeners.forEach((l) => l());
}

export function subscribeExploration(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getExplorationGeneration(): number {
  return generation;
}

export function getExploration(): ActiveExplorationState | null {
  return state;
}

export function getExplorationEpoch(): number {
  return epoch;
}

/** 开始/重绑探索会话（不清既有条件则保留 —— 重开视图时显式传入）。 */
export function bindExploration(
  sourceRef: string,
  sourceRevision: number,
  layerId: string,
  opts?: { charts?: ChartSpec[]; conditions?: ExplorationCondition[]; keep?: boolean },
): void {
  const prev = opts?.keep ? state : null;
  const charts = opts?.charts ?? (prev ? Array.from(prev.charts.values()) : []);
  const conditions = opts?.conditions
    ?? (prev ? Array.from(prev.conditions.values()) : []);
  state = {
    sourceRef,
    sourceRevision,
    layerId,
    charts: new Map(charts.map((c) => [c.chartId, c])),
    conditions: new Map(conditions.map((c) => [c.conditionId, c])),
  };
  emit();
}

export function unbindExploration(): void {
  if (!state) return;
  state = null;
  emit();
}

export function registerChart(chart: ChartSpec): void {
  if (!state) return;
  state.charts.set(chart.chartId, chart);
  emit();
}

export function removeChart(chartId: string): void {
  if (!state) return;
  state.charts.delete(chartId);
  // 该控件的条件一并移除（关闭自己的窗口 → 移除自己的条件，可解释策略）
  for (const [cid, cond] of state.conditions) {
    if (cond.chartId === chartId) state.conditions.delete(cid);
  }
  emit();
}

/** 设置/替换一个条件（同 conditionId 幂等替换；只影响该控件）。
 *
 * 空分类条件（无值且不含 null）= 控件 toggle-off 最后一个值 → 自动移除
 * （组件点击路径不需要单独调 removeCondition）。
 */
export function setCondition(cond: ExplorationCondition): void {
  if (!state) return;
  if (cond.kind === 'category' && cond.values.length === 0 && !cond.includeNull) {
    state.conditions.delete(cond.conditionId);
    emit();
    return;
  }
  state.conditions.set(cond.conditionId, cond);
  emit();
}

export function removeCondition(conditionId: string): void {
  if (!state?.conditions.has(conditionId)) return;
  state.conditions.delete(conditionId);
  emit();
}

/** 清空单个控件的条件（E05：与 clearAll 范围不同）。 */
export function clearChartConditions(chartId: string): void {
  if (!state) return;
  let changed = false;
  for (const [cid, cond] of state.conditions) {
    if (cond.chartId === chartId) {
      state.conditions.delete(cid);
      changed = true;
    }
  }
  if (changed) emit();
}

export function clearAllConditions(): void {
  if (!state || state.conditions.size === 0) return;
  state.conditions.clear();
  emit();
}

export function listConditions(): ExplorationCondition[] {
  return state ? Array.from(state.conditions.values()) : [];
}

export function listCharts(): ChartSpec[] {
  return state ? Array.from(state.charts.values()) : [];
}

export function getChartCondition(chartId: string): ExplorationCondition | null {
  if (!state) return null;
  for (const cond of state.conditions.values()) {
    if (cond.chartId === chartId) return cond;
  }
  return null;
}

/** 组合 where（表格下推 / preview 请求体 / 导出冻结共用同一编译）。 */
export function getCombinedWhere(excludeChartId?: string) {
  return compileCombinedWhere(listConditions(), excludeChartId);
}

/** 地图过滤投影：组合条件 → MapLibre 表达式（typed identity 精确）。 */
export function getExplorationFilterExpression(layerId: string): unknown[] | null {
  if (!state || state.layerId !== layerId) return null;
  return whereToMaplibre(compileCombinedWhere(listConditions()));
}

/** 会话切换世代 bump（异步核对）。 */
export function resetExplorationStore(): void {
  epoch += 1;
  state = null;
  emit();
}
