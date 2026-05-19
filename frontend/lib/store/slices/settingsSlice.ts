/**
 * Settings slice — 应用级配置：MCP 服务器 / 内置 skill / RAG / 地图样式 / LLM。
 *
 * 关注点：以"持久化的配置"为主（partialize 选中的大部分字段都在这里），
 * 包含一些遗留兼容字段（mcpConfig / llmConfig 字符串版）。
 */
import type { StateCreator } from 'zustand';
import {
  DEFAULT_MCP_SERVERS,
  DEFAULT_SKILLS,
  DEFAULT_MAP_STYLES,
} from '../../constants/demo';
import type { HudState } from '../hud-types';

export const createSettingsSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => ({
  /* ─── Settings UI 旧字段（legacy compat） ─── */
  settingsOpen: false,
  setSettingsOpen: (open: boolean) => set({ settingsOpen: open }),
  mcpConfig: '{}',
  setMcpConfig: (config: string) => set({ mcpConfig: config }),
  llmConfig: {},
  setLlmConfig: (config: Record<string, unknown>) => set({ llmConfig: config }),
  availableSkills: [],
  setAvailableSkills: (skills) => set({ availableSkills: skills }),

  /* ─── 持久化配置 ─── */
  mcpServers: DEFAULT_MCP_SERVERS,
  setMcpServers: (servers) => set({ mcpServers: servers }),
  toggleMcpServer: (id: string) =>
    set((s) => ({
      mcpServers: s.mcpServers.map((srv) =>
        srv.id === id
          ? { ...srv, status: srv.status === 'active' ? ('inactive' as const) : ('active' as const) }
          : srv,
      ),
    })),

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
