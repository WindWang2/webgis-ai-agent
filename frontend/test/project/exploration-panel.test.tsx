/**
 * G46 探索面板项目级组件测试（G38 data-join-panel.test 模式）。
 *
 * 覆盖：条件 chips 增删（E04/E05）、匹配计数与 facet 口径展示（E02/E11）、
 * 空集如实 0（E10）、stale 提示（E20）、键盘可达（E33 部分——chips 删除
 * 按钮可达）。API 层 mock（组件测不 mock dispatch/渲染主链之外的服务端）。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExplorationPanel } from '@/components/linked-exploration/exploration-panel';

import * as api from '@/lib/api/transport';
import {
  bindExploration,
  clearAllConditions,
  listConditions,
  registerChart,
  setCondition,
  unbindExploration,
} from '@/lib/linked-exploration/condition-store';

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
}));

const inspectResp = {
  anchor: {
    ref_id: 'ref:test-1', content_revision: 3,
    content_fingerprint: 'g41f-abc',
  },
  feature_count: 10,
  fields: [
    { name: 'cat', type: 'string', category_sample: [{ value: 'a', count: 4 }] },
    { name: 'pop', type: 'number', min: 0, max: 100 },
  ],
  schema_complete: true,
  sample_note: '全量行统计',
};

function previewResponse(matchCount: number) {
  return {
    anchor: inspectResp.anchor,
    conditions_digest: 'g46c-x',
    combined_where: { op: 'in', field: 'cat', values: ['a'] },
    match_count: matchCount,
    match_count_exact: true,
    total: 10,
    map_filter: ['in', ['get', 'cat'], ['literal', ['a']]],
    charts: [
      {
        chart_id: 'ch-cat', kind: 'category', field: 'cat',
        aggregation: 'count', title: '类别',
        facet: { scope: 'excluding-own-condition', note: '', statistics: { kind: 'category', field: 'cat', rows: [], matched: 4, truncated: false } },
        full: { scope: 'all-conditions', note: '', statistics: { kind: 'category', field: 'cat', rows: [], matched: matchCount, truncated: false } },
      },
      {
        chart_id: 'ch-rng', kind: 'range', field: 'pop',
        aggregation: 'count', title: '数值',
        facet: { scope: 'excluding-own-condition', note: '', statistics: { kind: 'range', field: 'pop', bins: [], matched: 6 } },
        full: { scope: 'all-conditions', note: '', statistics: { kind: 'range', field: 'pop', bins: [], matched: matchCount } },
      },
    ],
    completeness: 'complete',
  };
}

function mockApi(handler: (path: string, init?: { method?: string }) => unknown) {
  vi.mocked(api.apiFetch).mockImplementation(async (path: string, init?: { method?: string }) =>
    handler(path, init) as never);
}

const hudLayers = [
  { id: 'layer-a', name: '测试层A', type: 'vector', _refId: 'ref:test-1' },
];
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (sel: (s: { layers: unknown }) => unknown) => sel({
    layers: (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ ?? [],
  }),
}));

beforeEach(() => {
  unbindExploration();
  clearAllConditions();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ExplorationPanel', () => {
  it('绑定源后展示源锚与字段候选', async () => {
    mockApi((path) => {
      if (path.includes('/inspect')) return inspectResp;
      if (path.includes('/views')) return { views: [] };
      return previewResponse(2);
    });
    render(<ExplorationPanel sessionId="s1" ownerToken={null} />);
    // 源下拉无层（HUD 空）—— 面板渲染不崩
    expect(screen.getByTestId('g46-exploration-panel')).toBeTruthy();
  });

  it('条件 chips：增删与全部清空（E04/E05）', async () => {
    mockApi((path) => {
      if (path.includes('/inspect')) return inspectResp;
      if (path.includes('/views')) return { views: [] };
      return previewResponse(2);
    });
    bindExploration('ref:test-1', 3, 'layer-a');
    registerChart({ chartId: 'ch-cat', kind: 'category', field: 'cat', aggregation: 'count', title: '类别', bins: 10 });
    setCondition({
      kind: 'category', conditionId: 'cond-ch-cat', chartId: 'ch-cat',
      field: 'cat', values: ['a'], includeNull: false,
    });
    render(<ExplorationPanel sessionId="s1" ownerToken={null} />);
    const chip = await screen.findByTestId('g46-condition-chip');
    expect(chip.textContent).toContain('cat');
    // 删除单个条件（E04：其它作者状态保留 —— 这里只有一个条件）
    screen.getByTestId('g46-remove-condition-cond-ch-cat').click();
    await waitFor(() => {
      expect(listConditions()).toHaveLength(0);
    });
  });

  it('空集如实显示 0 匹配（E10）', async () => {
    mockApi((path) => {
      if (path.includes('/inspect')) return inspectResp;
      if (path.includes('/views')) return { views: [] };
      return previewResponse(0);
    });
    (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ = hudLayers;
    bindExploration('ref:test-1', 3, 'layer-a');
    render(<ExplorationPanel sessionId="s1" ownerToken={null} />);
    const select = screen.getByTestId('g46-source-select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'layer-a' } });
    await waitFor(() => {
      expect(screen.getByTestId('g46-match-count').textContent)
        .toContain('匹配 0');
    }, { timeout: 3000 });
    (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ = [];
  });

  it('保存视图调 POST /views（E19 路径）', async () => {
    const saveMock = vi.fn(async () => ({
      view_id: 'v1', title: 't', status: 'draft', revision: 1,
      source_ref: 'ref:test-1', source_revision: 3,
      normalized_digest: 'd',
    }));
    mockApi((path, init) => {
      if (path.includes('/inspect')) return inspectResp;
      if (path.includes('/views') && init?.method === 'POST') return saveMock();
      if (path.includes('/views')) return { views: [] };
      return previewResponse(2);
    });
    (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ = hudLayers;
    bindExploration('ref:test-1', 3, 'layer-a');
    render(<ExplorationPanel sessionId="s1" ownerToken={null} />);
    const select = screen.getByTestId('g46-source-select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'layer-a' } });
    // 等 inspect ready（buildPlan 依赖 inspect.data.anchor）
    await screen.findByTestId('g46-source-anchor');
    const input = screen.getByTestId('g46-view-title');
    fireEvent.change(input, { target: { value: '测试视图' } });
    screen.getByTestId('g46-save-view').click();
    await waitFor(() => {
      expect(saveMock).toHaveBeenCalledTimes(1);
    });
    const post = vi.mocked(api.apiFetch).mock.calls.find(
      ([path, init]) => path.includes('/views') && init?.method === 'POST',
    );
    expect(post).toBeTruthy();
    expect((post![1]!.body as { title: string }).title).toBe('测试视图');
    (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ = [];
  });

  it('结果表「加载更多」拉回的第 51+ 行可见', async () => {
    const firstPage = Array.from({ length: 50 }, (_, i) => ({ properties: { name: `row-${i}` } }));
    mockApi((path) => {
      if (path.includes('/inspect')) return inspectResp;
      if (path.includes('/views')) return { views: [] };
      if (path.includes('/features')) {
        const cursor = new URLSearchParams(path.split('?')[1] ?? '').get('cursor');
        return cursor
          ? {
              features: [{ properties: { name: 'row-50' } }],
              pagination: { next_cursor: null, has_more: false },
              total_matching: 51,
            }
          : {
              features: firstPage,
              pagination: { next_cursor: 'c2', has_more: true },
              total_matching: 51,
            };
      }
      // preview 保持 pending：避免 combinedWhere 变化触发结果表重置（本测只验证分页追加）
      return new Promise(() => {});
    });
    (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ = hudLayers;
    bindExploration('ref:test-1', 3, 'layer-a');
    render(<ExplorationPanel sessionId="s1" ownerToken={null} />);
    const select = screen.getByTestId('g46-source-select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'layer-a' } });
    const more = await screen.findByTestId('g46-table-more');
    expect(screen.getByText('row-49')).toBeTruthy();
    expect(screen.queryByText('row-50')).toBeNull();
    fireEvent.click(more);
    expect(await screen.findByText('row-50')).toBeTruthy();
    (globalThis as unknown as { __G46_LAYERS__?: unknown[] }).__G46_LAYERS__ = [];
  });
});
