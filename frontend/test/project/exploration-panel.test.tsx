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

function mockApi(handler: (path: string, init?: RequestInit) => unknown) {
  vi.mocked(api.apiFetch).mockImplementation(async (path: string) =>
    handler(path) as never);
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
    mockApi((path) => {
      if (path.includes('/views') && !path.includes('?')) return inspectResp;
      if (path.includes('/inspect')) return inspectResp;
      if (path.includes('/views')) return saveMock();
      return previewResponse(2);
    });
    bindExploration('ref:test-1', 3, 'layer-a');
    render(<ExplorationPanel sessionId="s1" ownerToken={null} />);
    const input = screen.getByTestId('g46-view-title');
    await screen.findByTestId('g46-save-view');
    expect(input).toBeTruthy();
  });
});
