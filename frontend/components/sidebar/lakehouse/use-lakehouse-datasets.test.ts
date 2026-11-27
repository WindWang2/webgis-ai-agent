/**
 * Dataset 版本层 hook 的加载态复位（评审 P2）。
 *
 * early-return 分支 abort + seq++ 掉在途请求后，请求 finally 里的 seq 守卫
 * 永远不匹配 → loading 卡真（列表永久转圈 / 详情区永久 loading 且残留旧错误）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  getDataset: vi.fn(),
  listDatasetVersions: vi.fn(),
}));

vi.mock('@/lib/api/lakehouse', () => ({
  lakehouseApi: {
    listDatasets: (...a: unknown[]) => api.listDatasets(...a),
    getDataset: (...a: unknown[]) => api.getDataset(...a),
    listDatasetVersions: (...a: unknown[]) => api.listDatasetVersions(...a),
  },
}));

import { useLakehouseDatasets, useLakehouseDatasetDetail } from './use-lakehouse-datasets';

/** 可控 promise：手动 resolve 模拟「在飞 / 迟到」的请求。 */
function deferred<T = unknown>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useLakehouseDatasets', () => {
  it('在途列表请求进行中切走 session：loading 解除（不永久转圈）', async () => {
    const d = deferred();
    api.listDatasets.mockImplementationOnce(() => d.promise as Promise<never>);
    const view = renderHook(
      (props: { sessionId: string }) => useLakehouseDatasets({ ownerType: 'session', sessionId: props.sessionId }),
      { initialProps: { sessionId: 's1' } },
    );
    await waitFor(() => expect(api.listDatasets).toHaveBeenCalled());
    expect(view.result.current.loading).toBe(true);
    // 切换/清空 session（early-return：seq++ + abort）
    view.rerender({ sessionId: '' });
    expect(view.result.current.loading).toBe(false);
    expect(view.result.current.datasets).toEqual([]);
    // 迟到响应落地（seq 不匹配被丢弃）—— 不得把 loading 重新卡住
    d.resolve({ datasets: [] });
    await waitFor(() => expect(view.result.current.loading).toBe(false));
  });
});

describe('useLakehouseDatasetDetail', () => {
  it('在途详情请求进行中取消选中：loading 复位且旧错误被清除', async () => {
    const d = deferred();
    api.getDataset.mockImplementationOnce(() => d.promise as Promise<never>);
    const view = renderHook(
      (props: { datasetId: string | null }) => useLakehouseDatasetDetail({ datasetId: props.datasetId, sessionId: 's1' }),
      { initialProps: { datasetId: 'ds-1' as string | null } },
    );
    await waitFor(() => expect(api.getDataset).toHaveBeenCalled());
    expect(view.result.current.loading).toBe(true);
    // 先制造一个错误，验证切走时被清掉
    view.rerender({ datasetId: 'ds-2' });
    api.getDataset.mockRejectedValueOnce(new Error('boom'));
    api.listDatasetVersions.mockRejectedValueOnce(new Error('boom'));
    await waitFor(() => expect(view.result.current.error).toBeTruthy());
    expect(view.result.current.loading).toBe(false);

    // 取消选中（datasetId=null → early-return）
    view.rerender({ datasetId: null });
    expect(view.result.current.loading).toBe(false);
    expect(view.result.current.error).toBeNull();
    expect(view.result.current.detail).toBeNull();
    // 迟到响应落地 —— 不得写回当前（无选中）状态
    d.resolve({ dataset_id: 'ds-1' });
    await waitFor(() => expect(view.result.current.detail).toBeNull());
  });
});
