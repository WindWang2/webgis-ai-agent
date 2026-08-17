/**
 * Settings slice — 应用级配置：内置 skill / RAG / 地图样式 / LLM。
 */
import type { StateCreator } from 'zustand';
import { DEFAULT_SKILLS } from '../../constants/demo';
import type { HudState } from '../hud-types';

export const createSettingsSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => ({
  /* ─── Settings UI 旧字段（legacy compat） ─── */
  settingsOpen: false,
  // UI V3 overlay 互斥：打开 settings 时关闭 history / templates drawer。
  setSettingsOpen: (open: boolean) =>
    set(open ? { settingsOpen: true, historyOpen: false, templatesOpen: false } : { settingsOpen: false }),
  llmConfig: {},
  setLlmConfig: (config: Record<string, unknown>) => set({ llmConfig: config }),
  availableSkills: [],
  setAvailableSkills: (skills) => set({ availableSkills: skills }),

  /* ─── 持久化配置 ─── */
  skills: DEFAULT_SKILLS,
  setSkills: (skills) => set({ skills }),

  /* ─── RAG ─── */
  ragInsight: null,
  setRagInsight: (insight) => set({ ragInsight: insight }),
  ragConfig: { vectorDb: '', collection: 'geoagent' },
  setRagConfig: (config) => set((s) => ({ ragConfig: { ...s.ragConfig, ...config } })),

  /* ─── LLM ─── */
  llmConfigFull: {
    baseUrl: 'https://api.openai.com/v1',
    apiKey: '',
    model: 'gpt-4o',
    caching: true,
  },
  setLlmConfigFull: (config) =>
    set((s) => ({ llmConfigFull: { ...s.llmConfigFull, ...config } })),
});
