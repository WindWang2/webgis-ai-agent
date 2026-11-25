/**
 * 空间目录分页：append 的 offset 必须按去重后实际新增数前进 —— 两页之间
 * 服务端插入新项时，按 page.length 前进会跳过边界处的真实条目。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

const api = vi.hoisted(() => ({ listSpatialCatalog: vi.fn() }));
const toast = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('@/lib/api/data-fabric', () => ({ dataFabricApi: api }));
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toast) => unknown) => selector(toast),
}));

import { useSpatialCatalog } from './use-spatial-catalog';

const item = (id: string) => ({
  id, source_id: 'src', name: id, title: id,
});

describe('useSpatialCatalog offset', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('append 页含重复项时 offset 只按实际新增数前进（不跳过条目）', async () => {
    api.listSpatialCatalog
      .mockResolvedValueOnce({ total: 4, limit: 50, offset: 0, items: [item('a'), item('b')] })
      // 第二页多出一条服务端在两页之间插入的新项 → b 重复出现
      .mockResolvedValueOnce({ total: 4, limit: 50, offset: 2, items: [item('b'), item('c')] })
      .mockResolvedValueOnce({ total: 4, limit: 50, offset: 3, items: [item('d')] });

    const { result } = renderHook(() => useSpatialCatalog());
    await waitFor(() => expect(api.listSpatialCatalog).toHaveBeenCalledTimes(1));
    await act(async () => { await result.current.loadMoreCatalog(); });
    await act(async () => { await result.current.loadMoreCatalog(); });

    const offsets = api.listSpatialCatalog.mock.calls.map(
      (call) => (call[0] as { offset?: number }).offset,
    );
    // 第三页应从 3 开始；未修复时 offset 按 page.length 累加到 4（跳过 c）
    expect(offsets).toEqual([0, 2, 3]);
  });
});
