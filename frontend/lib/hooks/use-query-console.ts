'use client';

import { create } from 'zustand';

/**
 * 查询控制台开关 store（ADR-0147）——独立于 useHudStore（§8：hud 既有
 * slice 语义多线共享，不改）。targetId 缺省时控制台保持在目标选择步。
 */
interface QueryConsoleState {
  open: boolean;
  /** 预选目录项 id（命令面板跳入时可带目标）。 */
  targetId: string | null;
  openWith: (targetId?: string | null) => void;
  close: () => void;
}

export const useQueryConsoleStore = create<QueryConsoleState>((set) => ({
  open: false,
  targetId: null,
  openWith: (targetId) => set({ open: true, targetId: targetId ?? null }),
  close: () => set({ open: false, targetId: null }),
}));
