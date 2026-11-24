'use client';

import { create } from 'zustand';

/**
 * 多源实体融合工作台开关 store —— 与 DataJoinPanel（G38）/查询控制台
 * （ADR-0147）同款独立 store：单例面板；open=false 时面板返回 null。
 */
interface DataFusionState {
  open: boolean;
  openPanel: () => void;
  close: () => void;
}

export const useDataFusionStore = create<DataFusionState>((set) => ({
  open: false,
  openPanel: () => set({ open: true }),
  close: () => set({ open: false }),
}));
