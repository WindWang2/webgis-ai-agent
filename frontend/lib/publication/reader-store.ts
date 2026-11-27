/**
 * 读者本地状态（WP3）——视口/显隐/筛选/选择。
 * 红线：全部局部状态，永不回写作者 MapSpec / 发布快照。
 */
import { create } from 'zustand';

import type { ReaderPredicate } from './reader-types';

export type ReaderStatus =
  | 'loading'
  | 'ready'
  | 'unavailable'   // 404：未发布/撤销/过期/错码（统一语义）
  | 'degraded'      // 有降级披露但可用
  | 'error';        // 网络/快照损坏

export interface ReaderSelection {
  layerId: string;
  featureId?: string | number;
  properties: Record<string, unknown>;
}

interface ReaderState {
  status: ReaderStatus;
  statusDetail: string;
  visibleLayers: Record<string, boolean>;
  predicates: ReaderPredicate[];
  selection: ReaderSelection | null;
  webglUnavailable: boolean;
  announce: string;
  setStatus: (status: ReaderStatus, detail?: string) => void;
  setVisibleLayers: (layers: Record<string, boolean>) => void;
  toggleLayer: (layerId: string) => void;
  setPredicates: (predicates: ReaderPredicate[], announceText?: string) => void;
  setSelection: (selection: ReaderSelection | null, announceText?: string) => void;
  setWebglUnavailable: (flag: boolean) => void;
}

export const useReaderStore = create<ReaderState>((set, get) => ({
  status: 'loading',
  statusDetail: '',
  visibleLayers: {},
  predicates: [],
  selection: null,
  webglUnavailable: false,
  announce: '',
  setStatus: (status, detail = '') => set({ status, statusDetail: detail }),
  setVisibleLayers: (layers) => set({ visibleLayers: layers }),
  toggleLayer: (layerId) => {
    const current = get().visibleLayers;
    const next = { ...current, [layerId]: !(current[layerId] ?? true) };
    set({ visibleLayers: next, announce: `图层已${next[layerId] ? '显示' : '隐藏'}：${layerId}` });
  },
  setPredicates: (predicates, announceText) => set({ predicates, announce: announceText ?? '' }),
  setSelection: (selection, announceText) => set({ selection, announce: announceText ?? '' }),
  setWebglUnavailable: (flag) => set({ webglUnavailable: flag }),
}));
