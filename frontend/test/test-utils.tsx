import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'
import { useHudStore } from '@/lib/store/useHudStore'
import type { HudState, TaskState, TaskStep } from '@/lib/store/useHudStore'
import type { Layer } from '@/lib/types/layer'

export function createMockTaskStep(overrides?: Partial<TaskStep>): TaskStep {
  return {
    id: 'step-001',
    tool: 'query_osm_poi',
    stepIndex: 0,
    status: 'completed',
    startedAt: Date.now() - 5000,
    completedAt: Date.now(),
    ...overrides,
  }
}

export function createMockTaskState(overrides?: Partial<TaskState>): TaskState {
  return {
    id: 'task-abc123def456',
    steps: [createMockTaskStep()],
    status: 'running',
    stepCount: 3,
    ...overrides,
  }
}

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
    analysisResult: null,
    currentTask: null,
    processLayers: {},
    viewport: { center: [116.4, 39.9] as [number, number], zoom: 10, bearing: 0, pitch: 0 },
    baseLayer: 'Carto Dark',
    is3D: false,
    _perceptionQueue: [],
    leftPanelOpen: true,
    rightPanelOpen: true,
    ragInsight: null,
    pendingSystemMessage: null,
    analysisAssets: [],
    settingsOpen: false,
    mcpConfig: '{}',
    llmConfig: {},
    availableSkills: [],
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    toggleLayer: vi.fn(),
    updateLayer: vi.fn(),
    reorderLayers: vi.fn(),
    clearLayers: vi.fn(),
    setEditingLayerId: vi.fn(),
    setAnalysisResult: vi.fn(),
    taskStart: vi.fn(),
    stepStart: vi.fn(),
    stepResult: vi.fn(),
    stepError: vi.fn(),
    taskComplete: vi.fn(),
    taskError: vi.fn(),
    taskCancelled: vi.fn(),
    clearTask: vi.fn(),
    addProcessLayer: vi.fn(),
    removeProcessLayer: vi.fn(),
    clearProcessLayers: vi.fn(),
    setViewport: vi.fn(),
    setBaseLayer: vi.fn(),
    setIs3D: vi.fn(),
    pushPerception: vi.fn(),
    drainPerception: vi.fn(() => []),
    toggleLeftPanel: vi.fn(),
    toggleRightPanel: vi.fn(),
    setRagInsight: vi.fn(),
    setPendingSystemMessage: vi.fn(),
    fetchAnalysisAssets: vi.fn(),
    updateAsset: vi.fn(),
    deleteAsset: vi.fn(),
    setSettingsOpen: vi.fn(),
    setMcpConfig: vi.fn(),
    setLlmConfig: vi.fn(),
    setAvailableSkills: vi.fn(),
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
