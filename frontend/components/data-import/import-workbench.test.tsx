/**
 * ImportWorkbench 组件测试 —— 恢复/有界渲染/配置流程（API 模块 mock）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

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

const session = vi.hoisted(() => {
  let current: { sessionId: string | undefined; ownerToken: string | null } = {
    sessionId: 'sess-1',
    ownerToken: null,
  };
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    set(next: { sessionId: string | undefined; ownerToken: string | null }) {
      current = next;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    reset() {
      current = { sessionId: 'sess-1', ownerToken: null };
    },
  };
});

vi.mock('@/lib/api/data-import', () => api);
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: vi.fn(() => session.get()),
  subscribeSessionIdentity: vi.fn((listener: () => void) => session.subscribe(listener)),
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
    session.reset();
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

  const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((res) => {
      resolve = res;
    });
    return { promise, resolve };
  };

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

  it('P0-1：勾选成员即时落服务端（saveImportPlan selected）并乐观更新', async () => {
    api.listImportBatches.mockResolvedValue([]);
    api.getImportBatch.mockResolvedValue({
      ...BATCH,
      members: [MEMBER, { ...MEMBER, member_key: 'f:src-2', display_name: 'b.csv' }],
    });
    api.saveImportPlan.mockResolvedValue({ batch_id: 'ib-111', plan_version: 1, plan_hashes: {}, status: 'configured' });
    api.listImportBatches.mockResolvedValue([
      { batch_id: 'ib-111', status: 'inspected', created_at: '', updated_at: '', expires_at: '',
        file_count: 2, member_count: 2, selected_count: 0, completed_count: 0, plan_version: 0,
        terminal: false, notes: [] },
    ]);
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    const checkbox = screen.getByTestId('member-select-f:src-1') as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    fireEvent.click(checkbox);
    await waitFor(() => expect(api.saveImportPlan).toHaveBeenCalledWith(
      'ib-111', 'sess-1', { selected: ['f:src-1'] }, expect.anything()
    ));
    // 乐观态：立即勾上
    await waitFor(() => {
      expect((screen.getByTestId('member-select-f:src-1') as HTMLInputElement).checked).toBe(true);
    });
    // 保存计划按钮不再因未选择而禁用
    const saveBtn = screen.getByText('保存解释计划').closest('button') as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(false);
  });

  it('提交轮询由 useBoundedPoll 驱动且仅在 committing 态启用', async () => {
    (useBoundedPoll as ReturnType<typeof vi.fn>).mockClear();
    api.listImportBatches.mockResolvedValue([]);
    render(<ImportWorkbench onClose={() => {}} />);
    await waitFor(() => expect(api.listImportBatches).toHaveBeenCalled());
    const opts = (useBoundedPoll as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0];
    expect(opts.enabled).toBe(false); // 无 active 批次
  });

  // P1：勾选成员后 active.members[].selected 仍是陈旧 false，两个 handler
  // 曾按它过滤，导致刚勾选成员的解释配置被整条丢弃（保存）/漏发（提交）。
  const selectMemberAndSetMode = async () => {
    fireEvent.click(screen.getByTestId('member-select-f:src-1'));
    await waitFor(() => expect(api.saveImportPlan).toHaveBeenCalledWith(
      'ib-111', 'sess-1', { selected: ['f:src-1'] }, expect.anything()
    ));
    fireEvent.change(screen.getByLabelText('解释模式'), { target: { value: 'points' } });
    fireEvent.change(screen.getByLabelText('源 CRS 声明'), { target: { value: 'EPSG:32650' } });
  };

  it('P1：保存解释计划带上刚勾选成员的配置（不按陈旧 m.selected 过滤）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    api.getImportBatch.mockResolvedValue({ ...BATCH, members: [MEMBER] });
    api.saveImportPlan.mockResolvedValue({ batch_id: 'ib-111', plan_version: 1, plan_hashes: {}, status: 'configured' });
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    await selectMemberAndSetMode();

    fireEvent.click(screen.getByText('保存解释计划').closest('button') as HTMLButtonElement);
    await waitFor(() => expect(api.saveImportPlan).toHaveBeenCalledTimes(2));
    const payload = api.saveImportPlan.mock.calls.at(-1)?.[2];
    expect(payload.selected).toEqual(['f:src-1']);
    expect(payload.member_configs['f:src-1']).toMatchObject({
      mode: 'points',
      source_crs_declared: 'EPSG:32650',
    });
  });

  it('P1：提交前落计划包含刚勾选成员的配置（解释不静默丢失）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    api.getImportBatch.mockResolvedValue({ ...BATCH, members: [MEMBER] });
    api.saveImportPlan.mockResolvedValue({ batch_id: 'ib-111', plan_version: 1, plan_hashes: {}, status: 'configured' });
    api.commitImportBatch.mockResolvedValue({ status: 'committing', receipts: {} });
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    await selectMemberAndSetMode();

    const commitBtn = screen.getByTestId('import-commit-btn');
    fireEvent.click(commitBtn);
    await new Promise((r) => setTimeout(r, 300)); // ConfirmAction MIN_ARM_MS
    fireEvent.click(commitBtn);
    await waitFor(() => expect(api.commitImportBatch).toHaveBeenCalled());
    const payload = api.saveImportPlan.mock.calls.at(-1)?.[2];
    expect(payload.selected).toEqual(['f:src-1']);
    expect(payload.member_configs['f:src-1']).toMatchObject({ mode: 'points' });
  });

  it('P2：切换会话后旧会话的批次列表迟到响应不得覆写当前列表', async () => {
    const stale = deferred<ReturnType<typeof listEntry>>();
    api.listImportBatches.mockImplementationOnce(() => stale.promise);
    api.listImportBatches.mockResolvedValue(listEntry('ib-new', 'inspected'));
    render(<ImportWorkbench onClose={() => {}} />);
    await waitFor(() => expect(api.listImportBatches).toHaveBeenCalledWith('sess-1', expect.anything()));

    act(() => {
      session.set({ sessionId: 'sess-2', ownerToken: null });
    });
    await waitFor(() => expect(api.listImportBatches).toHaveBeenCalledWith('sess-2', expect.anything()));
    expect(await screen.findByText(/ib-new/)).toBeTruthy();

    await act(async () => {
      stale.resolve(listEntry('ib-stale', 'inspected'));
      await Promise.resolve();
    });
    expect(screen.queryByText(/ib-stale/)).toBeNull();
    expect(screen.getByText(/ib-new/)).toBeTruthy();
  });

  it('P2：提交落计划剥离本地 dirty 标记（MemberConfig 合同）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    api.getImportBatch.mockResolvedValue({ ...BATCH, members: [MEMBER] });
    api.saveImportPlan.mockResolvedValue({ batch_id: 'ib-111', plan_version: 1, plan_hashes: {}, status: 'configured' });
    api.commitImportBatch.mockResolvedValue({ status: 'committing', receipts: {} });
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    await selectMemberAndSetMode();

    const commitBtn = screen.getByTestId('import-commit-btn');
    fireEvent.click(commitBtn);
    await new Promise((r) => setTimeout(r, 300)); // ConfirmAction MIN_ARM_MS
    fireEvent.click(commitBtn);
    await waitFor(() => expect(api.commitImportBatch).toHaveBeenCalled());
    const payload = api.saveImportPlan.mock.calls.at(-1)?.[2];
    expect(payload.member_configs['f:src-1']).toMatchObject({
      mode: 'points',
      source_crs_declared: 'EPSG:32650',
    });
    expect(payload.member_configs['f:src-1']).not.toHaveProperty('dirty');
  });

  it('P3：勾选落库后用最新 plan_version 做乐观锁（保存计划不误报版本冲突）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    // 加载时服务器版本 0；勾选落库后服务器版本 1（active 仍是加载时的快照）
    api.getImportBatch.mockResolvedValue({ ...BATCH, members: [MEMBER] });
    api.saveImportPlan.mockResolvedValue({ batch_id: 'ib-111', plan_version: 1, plan_hashes: {}, status: 'configured' });
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    await selectMemberAndSetMode();

    fireEvent.click(screen.getByText('保存解释计划').closest('button') as HTMLButtonElement);
    await waitFor(() => expect(api.saveImportPlan).toHaveBeenCalledTimes(2));
    const payload = api.saveImportPlan.mock.calls.at(-1)?.[2];
    expect(payload.expected_plan_version).toBe(1);
  });

  it('P3：提交前落计划带 expected_plan_version（不静默覆盖并发修改）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    api.getImportBatch.mockResolvedValue({ ...BATCH, plan_version: 3, members: [MEMBER] });
    api.saveImportPlan.mockResolvedValue({ batch_id: 'ib-111', plan_version: 3, plan_hashes: {}, status: 'configured' });
    api.commitImportBatch.mockResolvedValue({ status: 'committing', receipts: {} });
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    await selectMemberAndSetMode();

    const commitBtn = screen.getByTestId('import-commit-btn');
    fireEvent.click(commitBtn);
    await new Promise((r) => setTimeout(r, 300)); // ConfirmAction MIN_ARM_MS
    fireEvent.click(commitBtn);
    await waitFor(() => expect(api.commitImportBatch).toHaveBeenCalled());
    const payload = api.saveImportPlan.mock.calls.at(-1)?.[2];
    expect(payload.expected_plan_version).toBe(3);
  });

  it('P3：后一个勾选落库失败只回滚本次，不连坐此前已成功落库的勾选', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'inspected'));
    api.getImportBatch.mockResolvedValue({
      ...BATCH,
      members: [MEMBER, { ...MEMBER, member_key: 'f:src-2', display_name: 'b.csv' }],
    });
    api.saveImportPlan
      .mockResolvedValueOnce({ batch_id: 'ib-111', plan_version: 1, plan_hashes: {}, status: 'configured' })
      .mockRejectedValueOnce(new Error('save failed'));
    render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    fireEvent.click(screen.getByTestId('member-select-f:src-1'));
    await waitFor(() => expect(api.saveImportPlan).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('member-select-f:src-2'));
    await waitFor(() => expect(api.saveImportPlan).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId('import-workbench-error')).toBeTruthy());

    expect((screen.getByTestId('member-select-f:src-1') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId('member-select-f:src-2') as HTMLInputElement).checked).toBe(false);
  });

  it('P3：轮询响应缺 receipts 字段时按无收据处理（不因 Object.entries(undefined) 崩溃）', async () => {
    api.listImportBatches.mockResolvedValue(listEntry('ib-111', 'committing'));
    api.getImportBatch.mockResolvedValue({ ...BATCH, status: 'committing', members: [MEMBER] });
    const { rerender } = render(<ImportWorkbench onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ib-111/ }));
    await waitFor(() => expect(screen.getByText('table.csv')).toBeTruthy());

    (useBoundedPoll as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { status: 'committing' }, // 接口形状退化：receipts 字段缺失
      loading: false,
      error: null,
      refresh: vi.fn(),
      status: {},
    });
    rerender(<ImportWorkbench onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByTestId('import-receipts')).toBeNull());
    expect(screen.getByTestId('import-workbench')).toBeTruthy();
    (useBoundedPoll as ReturnType<typeof vi.fn>).mockImplementation(() => ({
      data: null, loading: false, error: null, refresh: vi.fn(), status: {},
    }));
  });
});
