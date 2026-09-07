import { describe, it, expect, beforeEach, vi } from 'vitest';
import { chartCommands } from './chartCommands';

// 命令单元测试只验证『提交了正确的补丁』；网络/CAS 通道由
// component-mutation 自身测试与集成覆盖。
const patchCalls: Array<{ componentId: string; patch: unknown }> = [];
vi.mock('@/lib/mapspec/component-mutation', () => ({
  commitComponentPatch: (componentId: string, patch: unknown) => {
    patchCalls.push({ componentId, patch });
    return Promise.resolve({ status: 'applied' });
  },
  getComponentPlacementOverride: () => undefined,
}));
import { resetSelectionStore, selectionEvents } from '@/lib/selection/selection-store';
import {
  getMapSpecSessionCursor,
  setMapSpecSessionCursor,
  commitMapSpecDocument,
} from '@/lib/mapspec/session-cursor';

beforeEach(() => {
  patchCalls.length = 0;
});

/**
 * V4 Agent 图表命令 —— 与用户 UI 共用 commitComponentPatch 突变通道。
 * 校验：chart_panel 过滤、状态迁移合法性、placement 投影、selection 广播。
 */

function seedSpec(components: unknown[]) {
  commitMapSpecDocument({
    version: '1.0',
    sources: {},
    layers: [],
    layout: { components },
  } as unknown as object,
  3,
  );
}

function ctxFor(params: Record<string, unknown>) {
  return {
    map: {},
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({}),
    setSelectedBaseLayer: () => {},
    command: 'chart_test',
    params: params as never,
  };
}

describe('chart agent commands', () => {
  beforeEach(() => {
    resetSelectionStore();
    const { sessionId } = getMapSpecSessionCursor();
    setMapSpecSessionCursor(sessionId ?? 'sess-test', 0, 'owner-test');
    seedSpec([
      {
        id: 'chart-1',
        type: 'chart_panel',
        enabled: true,
        placement: { mode: 'anchor', anchor: 'top-left' },
      },
      {
        id: 'legend-1',
        type: 'legend',
        enabled: true,
      },
    ]);
  });

  it('chart_set_state：合法迁移提交 placement 补丁', async () => {
    const res = await await chartCommands.chart_set_state.run(
      ctxFor({ componentId: 'chart-1', state: 'collapsed' }),
    ) as { status: string };
    expect(res.status).toBe('succeeded');
    expect(patchCalls).toContainEqual({
      componentId: 'chart-1',
      patch: {
        enabled: true,
        placement: { mode: 'anchor', anchor: 'top-left', collapsed: true },
      },
    });
  });

  it('chart_set_state：hidden → floating 非法迁移被拒', async () => {
    seedSpec([
      { id: 'chart-h', type: 'chart_panel', enabled: false },
    ]);
    const res = await await chartCommands.chart_set_state.run(
      ctxFor({ componentId: 'chart-h', state: 'floating' }),
    ) as { status: string; error?: string };
    expect(res.status).toBe('failed');
    expect(res.error).toContain('illegal');
  });

  it('非 chart_panel 组件被拒绝（不跨类型突变）', async () => {
    const res = await await chartCommands.chart_move.run(
      ctxFor({ componentId: 'legend-1', anchor: 'bottom-right' }),
    ) as { status: string; error?: string };
    expect(res.status).toBe('failed');
    expect(res.error).toContain('not found');
  });

  it('chart_move：floating x/y 投影', async () => {
    const res = await await chartCommands.chart_move.run(
      ctxFor({ componentId: 'chart-1', x: 120, y: 80 }),
    ) as { status: string; result?: unknown };
    expect(res.status).toBe('succeeded');
    // applyPatch 诚实回传已提交的补丁（含 enabled 与保留的既有字段）
    expect(patchCalls[0]?.patch).toEqual({
      enabled: true,
      placement: { mode: 'floating', anchor: 'top-left', x: 120, y: 80 },
    });
  });

  it('chart_switch_type：kind 变体通道', async () => {
    const res = await await chartCommands.chart_switch_type.run(
      ctxFor({ componentId: 'chart-1', chartType: 'stacked_bar' }),
    ) as { status: string };
    expect(res.status).toBe('succeeded');
  });

  it('chart_highlight：经 selection store 广播 chart→map', async () => {
    const res = await await chartCommands.chart_highlight.run(
      ctxFor({ componentId: 'chart-1', categories: ['A', 'B'] }),
    ) as { status: string };
    expect(res.status).toBe('succeeded');
    const events = selectionEvents();
    expect(events.length).toBeGreaterThan(0);
    expect(events[events.length - 1].source).toBe('chart');
    expect(events[events.length - 1].context?.selected_categories).toEqual(['A', 'B']);
  });

  it('chart_close / chart_restore：enabled 通道', async () => {
    expect(
      (await chartCommands.chart_close.run(ctxFor({ componentId: 'chart-1' })) as { status: string }).status,
    ).toBe('succeeded');
    expect(patchCalls.at(-1)).toEqual({ componentId: 'chart-1', patch: { enabled: false } });
    expect(
      (await chartCommands.chart_restore.run(ctxFor({ componentId: 'chart-1' })) as { status: string }).status,
    ).toBe('succeeded');
    expect(patchCalls.at(-1)).toEqual({ componentId: 'chart-1', patch: { enabled: true } });
  });
});
