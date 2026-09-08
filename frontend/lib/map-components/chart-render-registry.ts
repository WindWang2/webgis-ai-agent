/**
 * chart_panel 渲染事实注册表（V5 W5 rendered-state telemetry）。
 *
 * 数据通道：chart-panel 渲染器在「真实渲染出带数据的 series」或
 * 「降级/空数据」时发布状态；观测采集器（render-observation）读取
 * 一次性快照进 RenderObservation.charts —— 服务端 Map Product
 * Finalizer 由此做 chart_required 的数据级核验（V4 只有组件槽级）。
 *
 * 有界纪律：Map 上限 32 个组件 id（chrome 同时可见面板数远小于此）；
 * 超 LRU 驱逐最旧。SSR/测试环境零依赖（纯模块级 Map）。
 */

export interface ChartRenderState {
  /** 图表真实渲染出可视 series（空数据/错误降级 = false）。 */
  rendered: boolean;
  /** 当前渲染的数据点数（rendered=false 时为 0）。 */
  data_points: number;
  /** 数据仍在加载（非终态 —— 服务端按 warning 披露而非 error）。 */
  pending?: boolean;
}

const MAX_CHART_STATES = 32;

const states = new Map<string, ChartRenderState>();

export function registerChartRenderState(
  componentId: string,
  state: ChartRenderState,
): void {
  const id = String(componentId || '').slice(0, 64);
  if (!id) return;
  states.delete(id); // 重新插入 → LRU touch
  states.set(id, state);
  while (states.size > MAX_CHART_STATES) {
    const oldest = states.keys().next().value;
    if (oldest === undefined) break;
    states.delete(oldest);
  }
}

export function unregisterChartRenderState(componentId: string): void {
  states.delete(String(componentId || ''));
}

/** 观测采集用快照（rendered/data_points 两个小字段；有界）。 */
export function snapshotChartRenderStates(): Array<
  { id: string } & ChartRenderState
> {
  const out: Array<{ id: string } & ChartRenderState> = [];
  for (const [id, state] of states) {
    const entry: { id: string } & ChartRenderState = {
      id,
      rendered: state.rendered === true,
      data_points:
        typeof state.data_points === 'number'
          && Number.isFinite(state.data_points)
          ? Math.max(0, Math.floor(state.data_points))
          : 0,
    };
    if (state.pending === true) entry.pending = true;
    out.push(entry);
  }
  return out;
}

/** 测试隔离用清空。 */
export function clearChartRenderStates(): void {
  states.clear();
}
