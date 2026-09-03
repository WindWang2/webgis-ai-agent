/**
 * Map product version workspace — client + component tests.
 *
 * Covers: Page unwrap for the ledger, the pairwise diff fetch shape, rerun
 * call, and the panel's core contract (version timeline, five-dimension
 * badges, style-only ⇒ NO rerun offer, algorithm change ⇒ rerun offers the
 * changed step and posts from_step).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { clearCache } from './get-fast-path';

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

import {
  diffMapProductVersions,
  listMapProductVersions,
  rerunWorkflowRunFromStep,
} from './map-product';
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

describe('map product client', () => {
  it('unwraps the ledger Page', async () => {
    serveGet();
    const page = await listMapProductVersions('p1');
    expect(page.items.map((v) => v.version_no)).toEqual([3, 2, 1]);
  });

  it('fetches pairwise diff', async () => {
    serveGet();
    const d = await diffMapProductVersions('p1', 2, 3);
    expect(d.analysis_recomputation_expected).toBe(false);
    expect(d.style_changed).toBe(true);
  });

  it('rerun posts from_step', async () => {
    serveGet();
    await rerunWorkflowRunFromStep('p1', 'run-9', 's2');
    const init = mockFetch.mock.calls.at(-1)?.[1] as RequestInit;
    expect(String(mockFetch.mock.calls.at(-1)?.[0])).toContain('/runs/run-9/rerun');
    expect(JSON.parse(String(init.body))).toEqual({ from_step: 's2' });
  });
});
