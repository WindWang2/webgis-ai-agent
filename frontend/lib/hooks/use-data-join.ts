'use client';

import { create } from 'zustand';

/**
 * 数据关联工作台开关 store（G38）—— 与查询控制台（ADR-0147）同款独立
 * store：单例面板、可带预选源 ref 进入（从数据源页/属性表跳入）。
 */
interface DataJoinState {
  open: boolean;
  /** 预选左源 ref（跳入时可带；null = 从目标选择步开始）。 */
  presetLeftRef: string | null;
  openWith: (leftRef?: string | null) => void;
  close: () => void;
}

export const useDataJoinStore = create<DataJoinState>((set) => ({
  open: false,
  presetLeftRef: null,
  openWith: (leftRef) => set({ open: true, presetLeftRef: leftRef ?? null }),
  close: () => set({ open: false, presetLeftRef: null }),
}));
