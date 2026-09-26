/**
 * VisibilityPin — 可见层数据 pin 生命周期（F13，ADR-0214 D4）。
 *
 * 调度器缓存的字节预算 LRU 此前会把**正在显示**的层数据逐出（大图层
 * 场景下显示数据被预算压力挤掉 → 重新拉取/闪烁）。`pinRef`/`setPinned`
 * 语义早已实现并有测试锁定，但一直【预留未接线】。本模块补上接线：
 *
 *     可见层（layer.visible 且带 _refId）→ pin
 *     隐藏层 / 旧会话残留               → unpin
 *
 * 接线点（review P1 修复）：**模块级 HUD store 订阅**，而非挂载在条件
 * 渲染的 LayersTab 上 —— store 订阅与组件树无关，用户停在任意 tab 时
 * 显隐变化都同步 pin；会话切换时对旧会话做全量 unpin sweep（跨会话
 * 单例缓存的 pinned 集合不得单调增长）。
 *
 * 纪律：
 *
 * - **事件驱动**：zustand subscribe 只在 layers 真变化时触发；签名去重
 *   后仅显隐真变化时触碰缓存（幂等）；
 * - **revision 无关**：pin 走 `setRefPinned`（该 ref 全部缓存代次同
 *   pin）—— 显隐翻转/revision bump 不产生 pin 缺口；
 * - **诚实超账**：全部被 pin 且超预算时缓存如实超账（RefDataCache 契约，
 *   不装绿）；
 * - **fail-open**：调度器缺席（测试/SSR）→ 静默跳过，绝不影响渲染；
 *   不读不写 `layer.visible`（user-wins：本模块只做预算优化，绝不覆盖
 *   用户显隐）。
 */

import { useHudStore } from '@/lib/store/useHudStore';
import { getRefScheduler } from '@/lib/data-plane/ref-service';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

/** 稳定签名：pin 状态的身份（排序去重，键序无关；含 session）。 */
export function visibilityPinSignature(layers: Array<PickVisible>, sessionId: string): string {
  const pairs = layers
    .filter((l) => l._refId)
    .map((l) => `${String(l._refId)}:${l.visible !== false ? 1 : 0}`)
    .sort();
  return `${sessionId}|${pairs.join(',')}`;
}

type PickVisible = { _refId?: string; visible?: boolean };

/**
 * 同步一次 pin 状态（命令式入口；store 订阅消费它，测试直接调它）。
 * 返回 {pinned, unpinned} 计数（调度器条目触碰数，幂等重设同值也计入）。
 */
export function applyVisibilityPins(
  layers: Array<PickVisible>,
  sessionId: string,
): { pinned: number; unpinned: number } {
  let pinned = 0;
  let unpinned = 0;
  try {
    const scheduler = getRefScheduler();
    const seen = new Set<string>();
    for (const layer of layers) {
      const refId = layer._refId;
      if (!refId || seen.has(refId)) continue;
      seen.add(refId);
      if (layer.visible !== false) {
        pinned += scheduler.setRefPinned(sessionId, refId, true);
      } else {
        unpinned += scheduler.setRefPinned(sessionId, refId, false);
      }
    }
  } catch {
    // 调度器缺席/构造失败：pin 是预算优化，不阻断渲染链路。
  }
  return { pinned, unpinned };
}

let lastSyncedSession: string | undefined;
let lastSyncedSignature = '';

/**
 * HUD store → pin 的一次同步（store 订阅回调与测试共用）。
 * 会话切换：先对旧会话残留 unpin sweep，再按新会话同步。
 */
export function syncVisibilityPins(): void {
  const sessionId = getMapSpecSessionCursor().sessionId ?? '';
  const layers = useHudStore.getState().layers ?? [];
  if (lastSyncedSession !== undefined && lastSyncedSession !== sessionId) {
    try {
      getRefScheduler().unpinSession(lastSyncedSession);
    } catch {
      /* 调度器缺席 → sweep 跳过 */
    }
    lastSyncedSignature = '';
  }
  lastSyncedSession = sessionId;
  const signature = visibilityPinSignature(layers, sessionId);
  if (signature === lastSyncedSignature) return;
  lastSyncedSignature = signature;
  applyVisibilityPins(layers, sessionId);
}

let subscriptionStarted = false;

/**
 * 常驻订阅启动（幂等）。由 map-panel 静态 import 触发（地图在场的
 * 生命周期面）；store 订阅与组件树无关 —— 任意 tab 下显隐变化都同步。
 */
export function ensureVisibilityPinSubscription(): void {
  if (subscriptionStarted || typeof window === 'undefined') return;
  subscriptionStarted = true;
  try {
    useHudStore.subscribe(() => {
      syncVisibilityPins();
    });
  } catch {
    // 订阅失败只损失预算优化 —— 绝不影响渲染链路。
  }
}

// 模块加载即激活（map-panel 静态 import 本模块 → 地图在场即订阅）。
ensureVisibilityPinSubscription();

/** 测试重置（订阅一旦建立不退订 —— 模块级单例语义）。 */
export function _resetVisibilityPinForTests(): void {
  lastSyncedSession = undefined;
  lastSyncedSignature = '';
}
