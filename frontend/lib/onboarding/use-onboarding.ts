'use client';

import { create } from 'zustand';

/**
 * Onboarding 状态（ADR-0147 P6）：tour 进度 + hint 已读集。
 *
 * 持久化：`geoagent-onboarding-v1`（seen tour / hintsSeen 数组）。
 * 重看 tour = 重置 tourSeen 并打开；重置提示 = 清空 hintsSeen。
 */

const PERSIST_KEY = 'geoagent-onboarding-v1';

export interface OnboardingPersist {
  tourSeen: boolean;
  hintsSeen: string[];
}

export function loadPersist(): OnboardingPersist {
  if (typeof localStorage === 'undefined') return { tourSeen: false, hintsSeen: [] };
  try {
    const raw = localStorage.getItem(PERSIST_KEY);
    if (!raw) return { tourSeen: false, hintsSeen: [] };
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return { tourSeen: false, hintsSeen: [] };
    const obj = parsed as Partial<OnboardingPersist>;
    return {
      tourSeen: obj.tourSeen === true,
      hintsSeen: Array.isArray(obj.hintsSeen) ? obj.hintsSeen.filter((h): h is string => typeof h === 'string') : [],
    };
  } catch {
    return { tourSeen: false, hintsSeen: [] };
  }
}

export function savePersist(state: OnboardingPersist): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(PERSIST_KEY, JSON.stringify(state));
  } catch {
    /* 配额/隐私模式：降级为会话内态 */
  }
}

interface OnboardingState {
  /** tour 打开中。 */
  tourOpen: boolean;
  stepIndex: number;
  tourSeen: boolean;
  hintsSeen: string[];
  /** 当前展示的 hint id（null = 不展示）。 */
  activeHintId: string | null;
  hydrate: () => void;
  startTour: () => void;
  /** 完成或跳过：都视为已看过。 */
  finishTour: (completed: boolean) => void;
  nextStep: (total: number) => void;
  prevStep: () => void;
  markHintSeen: (id: string) => void;
  resetHints: () => void;
  setActiveHint: (id: string | null) => void;
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  tourOpen: false,
  stepIndex: 0,
  tourSeen: false,
  hintsSeen: [],
  activeHintId: null,
  hydrate: () => {
    const persisted = loadPersist();
    set({ tourSeen: persisted.tourSeen, hintsSeen: persisted.hintsSeen });
  },
  startTour: () => set({ tourOpen: true, stepIndex: 0 }),
  finishTour: (completed) => {
    const next = { tourSeen: true, hintsSeen: get().hintsSeen };
    savePersist(next);
    set({ tourOpen: false, stepIndex: 0, ...next });
    void completed;
  },
  nextStep: (total) => {
    const { stepIndex } = get();
    if (stepIndex + 1 >= total) get().finishTour(true);
    else set({ stepIndex: stepIndex + 1 });
  },
  prevStep: () => set((s) => ({ stepIndex: Math.max(0, s.stepIndex - 1) })),
  markHintSeen: (id) => {
    const hintsSeen = [...new Set([...get().hintsSeen, id])];
    savePersist({ tourSeen: get().tourSeen, hintsSeen });
    set({ hintsSeen, activeHintId: null });
  },
  resetHints: () => {
    savePersist({ tourSeen: get().tourSeen, hintsSeen: [] });
    set({ hintsSeen: [], activeHintId: null });
  },
  setActiveHint: (id) => set({ activeHintId: id }),
}));
