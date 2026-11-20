'use client';

import { create } from 'zustand';

/**
 * 联合探索工作台开关 store（G46，G38 同款模式）：单例面板、可带预选
 * ref/layer 跳入（从数据源页/命令面板进入）。
 */
interface LinkedExplorationState {
  open: boolean;
  presetRef: string | null;
  presetLayerId: string | null;
  openWith: (ref?: string | null, layerId?: string | null) => void;
  close: () => void;
}

export const useLinkedExplorationStore = create<LinkedExplorationState>((set) => ({
  open: false,
  presetRef: null,
  presetLayerId: null,
  openWith: (ref, layerId) => set({ open: true, presetRef: ref ?? null, presetLayerId: layerId ?? null }),
  close: () => set({ open: false, presetRef: null, presetLayerId: null }),
}));
