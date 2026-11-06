/**
 * ADR-0285 D2 三连收 emitter 单测：载荷形状/有界裁剪/mutation 链接引用。
 * 与 outbox 解耦（bus emit 的入队由 contract/outbox 测试覆盖）——这里
 * 断言铸造出的 event 形状（借 outbox 队列窥视缝）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  disposeInteractionBus,
  emitFeatureSelected,
  emitChartSelectionChanged,
  emitTableSelectionChanged,
  emitTemporalWindowSettled,
} from './bus';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { KIND_COALESCE_POLICY } from './contract';
import { publishSelection, resetSelectionStore } from '@/lib/selection/selection-store';

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: vi.fn(() => ({
    sessionId: 'sess-ir-1',
    ownerToken: 'tok',
  })),
}));

// 拦截 outbox enqueue：借 fetch 层窥视（outbox 默认 500ms debounce，这里
// 直接 mock fetch 抓 batch），更简单的是 mock './outbox' 的 enqueue。
vi.mock('./outbox', async (importOriginal) => {
  const orig = await importOriginal<typeof import('./outbox')>();
  return {
    ...orig,
    createInteractionOutbox: (options: unknown) => {
      const real = orig.createInteractionOutbox(options as never);
      return {
        ...real,
        enqueue: vi.fn((event: { kind: string; payload: Record<string, unknown> }) => {
          captured.push(event as CapturedEvent);
        }),
        dispose: real.dispose,
        flush: real.flush,
        flushKeepalive: real.flushKeepalive,
      };
    },
  };
});

interface CapturedEvent {
  kind: string;
  payload: Record<string, unknown>;
  client_mutation_id?: string;
  mutation_outcome?: string;
}

const captured: CapturedEvent[] = [];

beforeEach(() => {
  captured.length = 0;
  resetSelectionStore();
});

it.each(['map', 'table'] as const)('preserves the actual %s selection identity field for restore', (source) => {
  publishSelection('select', {
    source, layer_id: 'schools', selected_ids: ['sch-1'], id_field: 'OBJECTID',
  });
  const emit = source === 'map' ? emitFeatureSelected : emitTableSelectionChanged;
  emit({ layerId: 'schools', featureId: 'sch-1' });
  expect(captured[0].payload.id_field).toBe('OBJECTID');
});

afterEach(() => {
  disposeInteractionBus();
  vi.clearAllMocks();
});

describe('emitChartSelectionChanged', () => {
  it('carries layer/field/categories bounded to 8', () => {
    emitChartSelectionChanged({
      layerId: 'districts',
      field: 'type',
      categories: ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'],
    });
    expect(captured).toHaveLength(1);
    const event = captured[0];
    expect(event.kind).toBe('chart_selection_changed');
    expect(event.payload.layer_id).toBe('districts');
    expect((event.payload.categories as string[]).length).toBe(8);
    expect(event.payload.action).toBe('select');
  });

  it('clear action carries no categories', () => {
    emitChartSelectionChanged({ layerId: 'districts', action: 'clear' });
    expect(captured[0].payload.action).toBe('clear');
    expect(captured[0].payload.categories).toBeUndefined();
  });

  it('skips emission without a session', () => {
    (getMapSpecSessionCursor as ReturnType<typeof vi.fn>).mockReturnValueOnce({
      sessionId: undefined,
    });
    emitChartSelectionChanged({ layerId: 'x' });
    expect(captured).toHaveLength(0);
  });
});

describe('emitTableSelectionChanged', () => {
  it('carries feature id and select action', () => {
    emitTableSelectionChanged({ layerId: 'schools', featureId: 'sch-1' });
    expect(captured[0].kind).toBe('table_selection_changed');
    expect(captured[0].payload.feature_id).toBe('sch-1');
    expect(captured[0].payload.action).toBe('select');
  });

  it('clear action omits ids', () => {
    emitTableSelectionChanged({ layerId: 'schools', action: 'clear' });
    expect(captured[0].payload.feature_id).toBeUndefined();
    expect(captured[0].payload.action).toBe('clear');
  });
});

describe('emitTemporalWindowSettled', () => {
  it('is mutation-linked with outcome and coalesced kind', () => {
    expect(KIND_COALESCE_POLICY.temporal_window_settled).toBe('replace_pending');
    emitTemporalWindowSettled(
      { current: '2024-01-01', pinned: true, mode: 'pause' },
      { clientMutationId: 'mut-t1', outcome: 'committed', mutationRevision: 9 },
    );
    expect(captured[0].kind).toBe('temporal_window_settled');
    expect(captured[0].payload.current).toBe('2024-01-01');
    expect(captured[0].payload.pinned).toBe(true);
    expect(captured[0].client_mutation_id).toBe('mut-t1');
    expect(captured[0].mutation_outcome).toBe('committed');
  });

  it('omits absent facts rather than fabricating', () => {
    emitTemporalWindowSettled({}, { clientMutationId: 'mut-t2', outcome: 'failed' });
    expect(captured[0].payload.current).toBeUndefined();
    expect(captured[0].payload.mode).toBeUndefined();
    expect(captured[0].mutation_outcome).toBe('failed');
  });
});
