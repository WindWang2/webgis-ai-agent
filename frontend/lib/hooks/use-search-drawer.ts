'use client';

import { create } from 'zustand';

/**
 * 跨会话搜索抽屉开关（ADR-0147）——独立小 store，不进 useHudStore（§8）。
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
