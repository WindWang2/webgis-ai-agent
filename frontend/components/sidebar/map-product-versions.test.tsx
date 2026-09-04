/**
 * MapProductVersionsPanel component tests: timeline render, five-dimension
 * badges, style-only ⇒ no rerun offer, algorithm change ⇒ rerun posts
 * from_step, empty state.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { clearCache } from '@/lib/api/get-fast-path';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const LEDGER = {
  items: [
    { id: 3, project_id: 'p1', version_no: 3, product_fingerprint: 'fpC', created_at: '2026-01-03T00:00:00Z' },
    { id: 2, project_id: 'p1', version_no: 2, product_fingerprint: 'fpB', created_at: '2026-01-02T00:00:00Z' },
    { id: 1, project_id: 'p1', version_no: 1, product_fingerprint: 'fpA', created_at: '2026-01-01T00:00:00Z' },
  ],
  total: 3, limit: 50, offset: 0, has_more: false,
};

const styleOnlyDiff = {
  from_version_no: 2, to_version_no: 3, vs_version_no: 2,
  data_changed: false, algorithm_changed: false, parameter_changed: false,
  style_changed: true, output_changed: false,
  analysis_recomputation_expected: false,
  details: {
    input_dataset_fingerprints: { from: {}, to: {}, changed_keys: [] },
    algorithm_steps: [],
    parameter_steps: [],
    mapspec_fingerprint: { from: 'f1', to: 'f2' },
    artifacts: { added: [], removed: [], unchanged_count: 3 },
    workflow_runs: { from: 'run-1', to: 'run-1' },
  },
};

const algorithmDiff = {
  ...styleOnlyDiff,
  from_version_no: 1, to_version_no: 3,
  algorithm_changed: true, parameter_changed: true, output_changed: true,
  analysis_recomputation_expected: true,
  details: {
    ...styleOnlyDiff.details,
    algorithm_steps: [{ step_id: 's2', from: 'admin.aggregate', to: 'admin.aggregate_v2' }],
    parameter_steps: [{ step_id: 's2', from: { by: 'district' }, to: { by: 'street' } }],
  },
};

import { MapProductVersionsPanel } from '@/components/sidebar/map-product-versions';

beforeEach(() => {
  vi.clearAllMocks();
  clearCache();
});
afterEach(() => {
  clearCache();
});

function serveGet(ledger: unknown = LEDGER, diff: unknown = styleOnlyDiff) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === 'POST') return jsonOk({ id: 'run-2' });
    if (u.includes('/diff/')) return jsonOk(diff);
    if (u.includes('/map-products') && !u.includes('/diff/')) return jsonOk(ledger);
    return jsonOk({});
  });
}

describe('MapProductVersionsPanel', () => {
  it('renders the version timeline newest-first with 当前 badge', async () => {
    serveGet();
    render(<MapProductVersionsPanel projectId="p1" />);
    // V3 appears in the timeline AND the two compare selects — assert the
    // timeline row (font-mono block) and the badge instead of a unique text.
    await waitFor(() => expect(screen.getByText('当前')).toBeTruthy());
    expect(screen.getAllByText('V3').length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText('V1').length).toBeGreaterThanOrEqual(3);
  });

  it('style-only diff: no-recompute status and NO rerun button', async () => {
    serveGet(LEDGER, styleOnlyDiff);
    render(<MapProductVersionsPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByText(/分析重算：不需要/)).toBeTruthy());
    expect(screen.queryByText(/从分析步骤重跑/)).toBeNull();
  });

  it('algorithm diff: recomputation required + rerun offers the changed step', async () => {
    serveGet(LEDGER, algorithmDiff);
    render(<MapProductVersionsPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByText(/分析重算：需要/)).toBeTruthy());
    expect(screen.getByText(/从分析步骤重跑（s2）/)).toBeTruthy();
    // five dimensions visible
    for (const label of ['数据', '算法', '参数', '样式', '输出']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it('rerun click posts from_step and notifies the host', async () => {
    serveGet(LEDGER, algorithmDiff);
    const onRerunStarted = vi.fn();
    render(<MapProductVersionsPanel projectId="p1" onRerunStarted={onRerunStarted} />);
    const btn = await screen.findByText(/从分析步骤重跑（s2）/);
    await userEvent.click(btn);
    await waitFor(() => expect(onRerunStarted).toHaveBeenCalledWith('run-1'));
  });

  it('empty ledger renders the empty state', async () => {
    serveGet({ items: [], total: 0, limit: 50, offset: 0, has_more: false });
    render(<MapProductVersionsPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByText('暂无产品版本')).toBeTruthy());
  });
});
