/**
 * ViewportContext — 相机落定后的**有界空间上下文**（Runtime V4 / §12，
 * ADR-0091）。extent_change 事件的载荷宿主。
 *
 * 与 SelectionContext 的边界（ADR-0090 §2.5 的延续）：
 * - 视口不是选择 —— extent_change 绝不抢占 SelectionContext.current，
 *   二者是 sibling 模块，各自 latest-wins；
 * - 消费语义分级：cheap 派生统计（表格视口过滤、状态徽标）自动跟随；
 *   expensive GIS 分析（重聚合/重采样）**不订阅本上下文** —— 那属于
 *   product/action intent 决策，不属于交互运行时；
 * - 有界契约：bbox + zoom + fingerprint + generation，无几何无要素。
 *
 * 节流纪律（§12）：
 * - debounce（默认 300ms）：相机落定后静默期才发布（上游 map-panel 已有
 *   100ms 结算 debounce，这里是第二道防线，防止快速连续手势穿透）；
 * - fingerprint：量化 bbox+zoom 的指纹串，重复指纹不发布不 bump generation
 *   （重复抑制）；
 * - epoch：会话切换 bump，迟到的定时器回调核对后丢弃（stale cancellation）。
 */

export interface ViewportContext {
  bbox: [number, number, number, number];
  zoom: number;
  /** 发布世代（每次有效发布 +1；订阅方以此作 useSyncExternalStore 快照）。 */
  generation: number;
  at: number;
}

export const VIEWPORT_DEBOUNCE_MS = 300;
/** 指纹量化精度：bbox 按 1e-4 度（~10m）、zoom 按 0.01 级量化。 */
const BBOX_QUANTUM = 1e-4;
const ZOOM_QUANTUM = 1e-2;

let currentContext: ViewportContext | null = null;
let generation = 0;
let epoch = 0;
let pendingTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeViewportContext(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getViewportGeneration(): number {
  return generation;
}

export function getViewportContext(): ViewportContext | null {
  return currentContext;
}

/** 会话世代（定时器回调在 fire 时核对，跨会话的迟到结算直接丢弃）。 */
export function getViewportEpoch(): number {
  return epoch;
}

function fingerprint(bbox: [number, number, number, number], zoom: number): string {
  const q = (v: number) => Math.round(v / BBOX_QUANTUM);
  return `${q(bbox[0])},${q(bbox[1])},${q(bbox[2])},${q(bbox[3])}@${(Math.round(zoom / ZOOM_QUANTUM) * ZOOM_QUANTUM).toFixed(2)}`;
}

/**
 * 相机落定结算入口（debounce + 指纹去重 + epoch stale 取消）。
 * 返回 true 表示本次调用产生了一次有效发布（测试/诊断用）。
 */
export function publishViewportContext(
  bbox: [number, number, number, number],
  zoom: number,
  debounceMs: number = VIEWPORT_DEBOUNCE_MS,
): boolean {
  if (!bbox.every((v) => Number.isFinite(v)) || !Number.isFinite(zoom)) return false;
  const capturedEpoch = epoch;
  if (debounceMs <= 0) {
    return commit(bbox, zoom, capturedEpoch);
  }
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => {
    pendingTimer = null;
    commit(bbox, zoom, capturedEpoch);
  }, debounceMs);
  return false;
}

function commit(
  bbox: [number, number, number, number],
  zoom: number,
  capturedEpoch: number,
): boolean {
  if (capturedEpoch !== epoch) return false; // 会话已切换 —— 迟到结算丢弃
  const fp = fingerprint(bbox, zoom);
  if (currentContext && fingerprint(currentContext.bbox, currentContext.zoom) === fp) {
    return false; // 重复指纹抑制
  }
  currentContext = { bbox: [...bbox] as [number, number, number, number], zoom, generation: ++generation, at: Date.now() };
  emit();
  return true;
}

/** 立即结算挂起的 debounce（测试用）。 */
export function flushViewportContext(): void {
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
}

/** 会话切换：视口上下文属于当前会话。 */
export function resetViewportContext(): void {
  flushViewportContext();
  epoch += 1;
  if (currentContext) {
    currentContext = null;
    emit();
  }
}
