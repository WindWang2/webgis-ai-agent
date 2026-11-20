/** G20 像元检视的地图点击订阅（只广播坐标，不改变既有选择/草图语义）。 */

type Listener = (coord: [number, number]) => void;
const listeners = new Set<Listener>();

/** map-panel 的既有 click 处理链开头调用一次。 */
export function publishMapClick(coord: [number, number]): void {
  if (!Number.isFinite(coord[0]) || !Number.isFinite(coord[1])) return;
  for (const listener of Array.from(listeners)) {
    try {
      listener(coord);
    } catch {
      // 一个订阅者失败不影响其它工具/既有地图交互。
    }
  }
}

export function subscribeMapClick(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function resetMapClickListeners(): void {
  listeners.clear();
}
