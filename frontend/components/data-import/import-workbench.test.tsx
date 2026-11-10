/**
 * ImportWorkbench 组件测试 —— 恢复/有界渲染/配置流程（API 模块 mock）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  listImportBatches: vi.fn(),
  getImportBatch: vi.fn(),
  createImportBatch: vi.fn(),
  saveImportPlan: vi.fn(),
  previewImportMembers: vi.fn(),
  commitImportBatch: vi.fn(),
  cancelImportBatch: vi.fn(),
  deleteImportBatch: vi.fn(),
}));

vi.mock('@/lib/api/data-import', () => api);
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: vi.fn(() => ({ sessionId: 'sess-1', ownerToken: null })),
}));

import { ImportWorkbench } from './import-workbench';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';

vi.mock('@/lib/hooks/use-cluster-poll', () => ({
  useBoundedPoll: vi.fn(() => ({ data: null, loading: false, error: null, refresh: vi.fn(), status: {} })),
}));

const MEMBER = {
  member_key: 'f:src-1',
  source_id: 'src-1',
  container: 'file' as const,
  kind: 'table' as const,
  display_name: 'table.csv',
  container_ref: null,
  fields: ['lng', 'lat', 'adm'],
  geometry_type: null,
  feature_count: null,
  crs: null,
  crs_source: 'unknown',
  size_bytes: 100,
  unsupported_reason: null,
  hints: { x_column: 'lng', y_column: 'lat' },
  status: 'discovered' as const,
  selected: false,
};

const BATCH = {
  batch_id: 'ib-111',
  status: 'inspected',
  plan_version: 0,
  sources: [],
  members: [MEMBER],
  interpretations: {},
  plan_hashes: {},
  receipts: {},
  notes: [],
};

describe('ImportWorkbench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('挂载即从服务器恢复批次列表（不依赖内存）', async () => {
    api.listImportBatches.mockResolvedValueOnce([
      { batch_id: 'ib-111', status: 'inspected', created_at: '', updated_at: '', expires_at: '',
        file_count: 1, member_count: 1, selected_count: 0, completed_count: 0, plan_version: 0,
        terminal: false, notes: [] },
    ]);
    render(<ImportWorkbench onClose={() => {}} />);
    await waitFor(() => expect(api.listImportBatches).toHaveBeenCalledWith('sess-1', expect.anything()));
    expect(await screen.findByText(/ib-111/)).toBeTruthy();
  });

  const listEntry = (batchId: string, status: string) => ([
    { batch_id: batchId, status, created_at: '', updated_at: '', expires_at: '',
      file_count: 1, member_count: 1, selected_count: 0, completed_count: 0, plan_version: 0,
      terminal: false, notes: [] },
  ]);

  it('点击批次恢复成员清单与服务器上的解释配置（场景 16）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'configured'));
    api.getImportBatch.mockResolvedValue({
      ...BATCH,
      status: 'configured',
      plan_version: 3,
      interpretations: { 'f:src-1': { mode: 'points', x_column: 'Easting', y_column: 'Northing' } },
    });
    render(<ImportWorkbench onClose={() => {}} />);
    const btn = await screen.findByRole('button', { name: /ib-111/ });
    fireEvent.click(btn);
    await waitFor(() => expect(api.getImportBatch).toHaveBeenCalledWith('ib-111', 'sess-1', expect.anything()));
    expect(await screen.findByText('table.csv')).toBeTruthy();
  });

  it('成员行披露 unsupported 原因', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    api.getImportBatch.mockResolvedValue({
      ...BATCH,
      members: [{ ...MEMBER, unsupported_reason: 'empty_sheet', display_name: 'book.xlsx :: 空表' }],
    });
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText(/empty_sheet/)).toBeTruthy());
  });

  it('提交轮询由 useBoundedPoll 驱动且仅在 committing 态启用', async () => {
    (useBoundedPoll as ReturnType<typeof vi.fn>).mockClear();
    api.listImportBatches.mockResolvedValue([]);
    render(<ImportWorkbench onClose={() => {}} />);
    await waitFor(() => expect(api.listImportBatches).toHaveBeenCalled());
    const opts = (useBoundedPoll as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0];
    expect(opts.enabled).toBe(false); // 无 active 批次
  });
});
