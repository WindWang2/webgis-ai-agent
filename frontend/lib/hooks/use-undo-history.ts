'use client';

import { create } from 'zustand';

/**
 * 操作历史弹层开关（ADR-0147 P5）——独立 store（§8：不动 hud 语义）。
 */
interface UndoHistoryState {
  isOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
}

export const useUndoHistoryStore = create<UndoHistoryState>((set) => ({
  isOpen: false,
  openPanel: () => set({ isOpen: true }),
  closePanel: () => set({ isOpen: false }),
}));
