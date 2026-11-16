'use client';

import { create } from 'zustand';

/**
 * 坐标操作工作台开关 store（G45）—— 与数据关联工作台（G38）同款独立
 * store：单例面板、可带预选源 ref 进入（从数据源页跳入）。
 */
interface CoordinateOperationsState {
  open: boolean;
  /** 预选源 ref（跳入时可带；null = 从源选择步开始）。 */
  presetSourceRef: string | null;
  openWith: (sourceRef?: string | null) => void;
  close: () => void;
}

export const useCoordinateOperationsStore = create<CoordinateOperationsState>((set) => ({
  open: false,
  presetSourceRef: null,
  openWith: (sourceRef) => set({ open: true, presetSourceRef: sourceRef ?? null }),
  close: () => set({ open: false, presetSourceRef: null }),
}));
