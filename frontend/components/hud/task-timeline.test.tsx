import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TaskTimeline } from './task-timeline';
import type { TaskState, TaskStep } from '@/lib/store/useHudStore';

const addProcessLayer = vi.fn();
const removeProcessLayer = vi.fn();
let _currentTask: TaskState | null = null;

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector({
    currentTask: _currentTask,
    addProcessLayer,
    removeProcessLayer,
  }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../../test/__mocks__/framer-motion');
  return { motion: fm.motion, AnimatePresence: fm.AnimatePresence };
});

function createMockTaskStep(overrides?: Partial<TaskStep>): TaskStep {
  return {
    id: 'step-001',
    tool: 'query_osm_poi',
    stepIndex: 0,
    status: 'completed',
    startedAt: Date.now() - 5000,
    completedAt: Date.now(),
    ...overrides,
  };
}

function createMockTaskState(overrides?: Partial<TaskState>): TaskState {
  return {
    id: 'task-abc123def456',
    steps: [createMockTaskStep()],
    status: 'running',
    stepCount: 3,
    ...overrides,
  };
}

describe('TaskTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _currentTask = null;
  });

  it('renders nothing when currentTask is null', () => {
    const { container } = render(<TaskTimeline />);
    expect(container.innerHTML).toBe('');
  });

  it('renders task header with step count', () => {
    _currentTask = createMockTaskState({
      steps: [
        createMockTaskStep({ id: 's1', status: 'completed' }),
        createMockTaskStep({ id: 's2', status: 'running' }),
        createMockTaskStep({ id: 's3', status: 'pending' }),
      ],
    });
    const { container } = render(<TaskTimeline />);
    // Component renders "{id.slice(0,8)} · {steps.length} steps"
    expect(container.textContent).toContain('steps');
    expect(container.textContent).toContain('3');
  });

  it('renders tool labels via TOOL_LABELS mapping', () => {
    _currentTask = createMockTaskState({
      steps: [createMockTaskStep({ tool: 'query_osm_poi', status: 'completed' })],
    });
    render(<TaskTimeline />);
    expect(screen.getByText('POI 查询')).toBeInTheDocument();
  });

  it('shows running spinner for running step', () => {
    _currentTask = createMockTaskState({
      steps: [createMockTaskStep({ status: 'running' })],
    });
    render(<TaskTimeline />);
    expect(document.querySelector('.animate-spin')).toBeInTheDocument();
  });

  it('shows checkmark for completed step', () => {
    _currentTask = createMockTaskState({
      steps: [createMockTaskStep({ status: 'completed' })],
    });
    render(<TaskTimeline />);
    expect(document.querySelector('.text-hud-green')).toBeInTheDocument();
  });

  it('shows elapsed time for completed steps', () => {
    const now = Date.now();
    _currentTask = createMockTaskState({
      steps: [createMockTaskStep({
        status: 'completed',
        startedAt: now - 2500,
        completedAt: now,
      })],
    });
    render(<TaskTimeline />);
    expect(screen.getByText('2.5s')).toBeInTheDocument();
  });

  it('shows GeoJSON button when step has geojson snapshot', () => {
    _currentTask = createMockTaskState({
      steps: [createMockTaskStep({
        status: 'completed',
        hasGeojson: true,
        geojsonSnapshot: { type: 'FeatureCollection', features: [] },
      })],
    });
    render(<TaskTimeline />);
    expect(screen.getByText('GeoJSON')).toBeInTheDocument();
  });

  it('calls addProcessLayer when GeoJSON button clicked', () => {
    _currentTask = createMockTaskState({
      steps: [createMockTaskStep({
        status: 'completed',
        hasGeojson: true,
        geojsonSnapshot: { type: 'FeatureCollection', features: [] },
      })],
    });
    render(<TaskTimeline />);
    fireEvent.click(screen.getByText('GeoJSON'));
    expect(addProcessLayer).toHaveBeenCalledTimes(1);
  });
});
