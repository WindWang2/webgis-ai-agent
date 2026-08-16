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
  toggleSkill: (id: string) =>
    set((s) => ({
      skills: s.skills.map((sk) => (sk.id === id ? { ...sk, enabled: !sk.enabled } : sk)),
    })),

  /* ─── RAG ─── */
  ragInsight: null,
  setRagInsight: (insight) => set({ ragInsight: insight }),
  ragConfig: { spatialWeight: 60, topK: 5, rerank: true, vectorDb: '', collection: 'geoagent' },
  setRagConfig: (config) => set((s) => ({ ragConfig: { ...s.ragConfig, ...config } })),
  ragSpatial: [],
  setRagSpatial: (docs) => set({ ragSpatial: docs }),
  ragSemantic: [],
  setRagSemantic: (docs) => set({ ragSemantic: docs }),

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
