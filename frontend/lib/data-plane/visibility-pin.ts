/**
 * VisibilityPin — 可见层数据 pin 生命周期（F13，ADR-0214 D4）。
 *
 * 调度器缓存的字节预算 LRU 此前会把**正在显示**的层数据逐出（大图层
 * 场景下显示数据被预算压力挤掉 → 重新拉取/闪烁）。`pinRef`/`setPinned`
 * 语义早已实现并有测试锁定，但一直【预留未接线】。本模块补上接线：
 *
 *     可见层（layer.visible 且带 _refId）→ pin
 *     隐藏层 / 会话外 ref               → unpin
 *
 * 纪律：
 *
 * - **幂等 + 变更驱动**：effect 以 (sessionId, refId, visible) 稳定签名
 *   去重 —— 仅显隐真变化时同步 pin（不是每渲染全量重刷）；
 * - **revision 无关**：pin 走 `setRefPinned`（该 ref 全部缓存代次同
 *   pin）—— 显隐翻转/revision bump 不产生 pin 缺口；
 * - **诚实超账**：全部被 pin 且超预算时缓存如实超账（RefDataCache 契约，
 *   不装绿）；
 * - **fail-open**：调度器缺席（测试/SSR）→ 静默跳过，绝不影响渲染。
 */

import { useEffect, useRef } from 'react';
import type { Layer } from '@/lib/types/layer';
import { getRefScheduler } from '@/lib/data-plane/ref-service';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

/** 稳定签名：可见性状态的身份（排序去重，键序无关）。 */
export function visibilityPinSignature(layers: Layer[], sessionId: string): string {
  const pairs = layers
    .filter((l) => l._refId)
    .map((l) => `${String(l._refId)}:${l.visible !== false ? 1 : 0}`)
    .sort();
  return `${sessionId}|${pairs.join(',')}`;
}

/**
 * 同步一次 pin 状态（命令式入口；hook 消费它，测试直接调它）。
 * 返回 {pinned, unpinned} 条目计数（调度器级幂等去重后的真实触碰数）。
 */
export function applyVisibilityPins(layers: Layer[]): {
  pinned: number;
  unpinned: number;
} {
  const sessionId = getMapSpecSessionCursor().sessionId ?? '';
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

/**
 * Layer Manager 订阅点：图层显隐变化 → 可见层 pin / 隐藏层 unpin。
 * 签名不变时零操作（重渲染安全）。
 */
export function useRefVisibilityPins(layers: Layer[]): void {
  const signatureRef = useRef<string>('');
  useEffect(() => {
    const sessionId = getMapSpecSessionCursor().sessionId ?? '';
    const signature = visibilityPinSignature(layers, sessionId);
    if (signature === signatureRef.current) return;
    signatureRef.current = signature;
    applyVisibilityPins(layers);
  });
}
