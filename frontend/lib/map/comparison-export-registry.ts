/**
 * W8（ADR-0118）：swipe 对比导出组合 registry —— module 级单活跃对比态。
 *
 * ComparisonView 把第二地图实例的 canvas 访问器与当前 comparison 状态
 * （kind/position）注册进来；exporter 导出时据此组合副图视图（此前对比态
 * 导出静默只截主图）。防泄漏：只持 canvas 访问器闭包（不持 Map 实例），
 * ComparisonView 关闭/卸载即 clear —— registry 为空 = 无对比态。
 */

export interface ComparisonExportState {
  /** 副图渲染 canvas 访问器（未挂载/未就绪 → null）。 */
  getSecondCanvas: () => HTMLCanvasElement | null;
  /** 对比形态词表（当前仅 swipe；side-by-side 已诚实下线）。 */
  kind: string;
  /** swipe 分界比例（0..1，右半为副图 —— 与 live clip-path inset 同侧）。 */
  position: number;
}

let registry: ComparisonExportState | null = null;

/** 注册/覆盖当前活跃对比态（ComparisonView 激活期间调用）。 */
export function setComparisonExport(state: ComparisonExportState): void {
  registry = state;
}

/** 导出侧读取（null = 无活跃对比态）。 */
export function getComparisonExport(): ComparisonExportState | null {
  return registry;
}

/** 清除注册（ComparisonView 关闭/卸载时调用 —— 不持泄漏引用）。 */
export function clearComparisonExport(): void {
  registry = null;
}
