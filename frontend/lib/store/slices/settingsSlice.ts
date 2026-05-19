/**
 * Settings slice — 应用级配置：内置 skill / RAG / 地图样式 / LLM。
 */
import type { StateCreator } from 'zustand';
import {
  DEFAULT_SKILLS,
  DEFAULT_MAP_STYLES,
} from '../../constants/demo';
import type { HudState } from '../hud-types';

export const createSettingsSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => ({
  /* ─── Settings UI 旧字段（legacy compat） ─── */
  settingsOpen: false,
  setSettingsOpen: (open: boolean) => set({ settingsOpen: open }),
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

  /* ─── Map Styles ─── */
  mapStyles: DEFAULT_MAP_STYLES,
  setMapStyles: (styles) => set({ mapStyles: styles }),

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
