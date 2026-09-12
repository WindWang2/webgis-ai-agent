'use client';

import { create } from 'zustand';

/**
 * 搜索抽屉开关 + 「定位到消息」pending 通道（ADR-0147）。
 *
 * pendingLocate：搜索命中 → 打开历史会话后，由 chat 侧在消息装载完成时
 * 消费，滚动到目标消息并短暂高亮。会话不匹配则丢弃（防跨会话串位）。
 */

interface SearchDrawerState {
  isOpen: boolean;
  openDrawer: () => void;
  closeDrawer: () => void;
}

export const useSearchDrawerStore = create<SearchDrawerState>((set) => ({
  isOpen: false,
  openDrawer: () => set({ isOpen: true }),
  closeDrawer: () => set({ isOpen: false }),
}));

let pendingLocate: { sessionId: string; messageIndex: number; issuedAt: number } | null = null;
const LOCATE_TTL_MS = 15_000;

export function setPendingLocate(sessionId: string, messageIndex: number): void {
  pendingLocate = { sessionId, messageIndex, issuedAt: Date.now() };
}

export function consumePendingLocate(sessionId: string): number | null {
  if (!pendingLocate || pendingLocate.sessionId !== sessionId) return null;
  if (Date.now() - pendingLocate.issuedAt > LOCATE_TTL_MS) {
    pendingLocate = null;
    return null;
  }
  const index = pendingLocate.messageIndex;
  pendingLocate = null;
  return index;
}
