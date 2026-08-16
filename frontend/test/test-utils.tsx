import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'
import { useHudStore } from '@/lib/store/useHudStore'
import type { HudState, AiStatus, LeftTab, SettingsTab } from '@/lib/store/useHudStore'
import type { Layer } from '@/lib/types/layer'

export function createMockLayer(overrides?: Partial<Layer>): Layer {
  return {
    id: 'layer-001',
    name: 'Test Layer',
    type: 'vector',
    visible: true,
    opacity: 0.8,
    ...overrides,
  }
}

export function createMockStoreState(overrides?: Partial<HudState>): Record<string, unknown> {
  return {
    layers: [],
    editingLayerId: null,
    currentTask: null,
    processLayers: {},
    viewport: { center: [116.4, 39.9] as [number, number], zoom: 10, bearing: 0, pitch: 0 },
    baseLayer: 'Carto Light',
    is3D: false,
    mapLoaded: false,
    // _perceptionQueue 已移除（useWebSocket hook 死代码删除）
    leftPanelOpen: true,
    rightPanelOpen: true,
    ragInsight: null,
    pendingSystemMessage: null,
    _systemMessageQueue: [],
    analysisAssets: [],
    settingsOpen: false,
    llmConfig: {},
    availableSkills: [],
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    toggleLayer: vi.fn(),
    updateLayer: vi.fn(),
    reorderLayers: vi.fn(),
    clearLayers: vi.fn(),
    setEditingLayerId: vi.fn(),
    setMapLoaded: vi.fn(),
    clearTask: vi.fn(),
    addProcessLayer: vi.fn(),
    removeProcessLayer: vi.fn(),
    clearProcessLayers: vi.fn(),
    setViewport: vi.fn(),
    setBaseLayer: vi.fn(),
    setIs3D: vi.fn(),
    // pushPerception / drainPerception 已移除（useWebSocket hook 死代码删除）
    toggleLeftPanel: vi.fn(),
    toggleRightPanel: vi.fn(),
    setRagInsight: vi.fn(),
    setPendingSystemMessage: vi.fn(),
    drainSystemMessage: vi.fn(),
    fetchAnalysisAssets: vi.fn(),
    updateAsset: vi.fn(),
    deleteAsset: vi.fn(),
    setSettingsOpen: vi.fn(),
    setLlmConfig: vi.fn(),
    setAvailableSkills: vi.fn(),
    aiStatus: 'idle' as AiStatus,
    setAiStatus: vi.fn(),
    activeLeftTab: 'chat' as LeftTab,
    setActiveLeftTab: vi.fn(),
    historyOpen: false,
    setHistoryOpen: vi.fn(),
    templatesOpen: false,
    setTemplatesOpen: vi.fn(),
    settingsTab: 'llm' as SettingsTab,
    setSettingsTab: vi.fn(),
    sessions: [],
    setSessions: vi.fn(),
    skills: [],
    setSkills: vi.fn(),
    ragConfig: { vectorDb: '', collection: 'geoagent' },
    setRagConfig: vi.fn(),
    llmConfigFull: { baseUrl: 'https://api.openai.com/v1', apiKey: '', model: 'gpt-4o', caching: true },
    setLlmConfigFull: vi.fn(),
    // 审计 PR 4: session 切换状态清理用到这些 setter —— mock 必须暴露
    selectedFeature: null,
    setSelectedFeature: vi.fn(),
    annotations: [],
    addAnnotation: vi.fn(),
    clearAnnotations: vi.fn(),
    // Analysis Results Workbench slice
    results: [],
    selectedResultId: null,
    captureToolCallArgs: vi.fn(),
    captureStepResult: vi.fn(),
    enrichResultOutput: vi.fn(),
    selectResult: vi.fn(),
    removeResult: vi.fn(),
    clearResults: vi.fn(),
    ...overrides,
  }
}

export function renderWithStore(
  ui: ReactElement,
  storeState?: Partial<HudState>,
  options?: Omit<RenderOptions, 'wrapper'>
) {
  const fullState = createMockStoreState(storeState)
  vi.spyOn(useHudStore, 'getState').mockReturnValue(fullState as unknown as HudState)
  return render(ui, options)
}
