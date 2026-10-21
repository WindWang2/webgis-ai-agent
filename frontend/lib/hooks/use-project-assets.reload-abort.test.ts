import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

/**
 * #1746-3：useProjectArtifacts.reload 的 AbortController 必须入 ref。
 *
 * 此前 reload 每次新建 AbortController 却从不存入 ref（对照同文件
 * loadLineage 的 lineageAbort），卸载/切项目时无法中止在途的产物列表请求 ——
 * 违背本模块头部「unmount aborts everything」契约。
 */

const fetchProjectArtifactsMock = vi.hoisted(() => vi.fn());
const fetchArtifactLineageMock = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api/project-assets', () => ({
  fetchProjectArtifacts: fetchProjectArtifactsMock,
}));
vi.mock('@/lib/api/project', () => ({
  fetchArtifactLineage: fetchArtifactLineageMock,
}));

import { useProjectArtifacts } from './use-project-assets';

const PAGE = { items: [], total: 0, limit: 20, offset: 0, has_more: false };

describe('useProjectArtifacts reload 可中止', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchProjectArtifactsMock.mockResolvedValue(PAGE);
  });

  it('卸载时中止在途的产物列表请求', async () => {
    let signal: AbortSignal | undefined;
    fetchProjectArtifactsMock.mockImplementationOnce(
      (_projectId: string, opts?: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal = opts?.signal;
          // 永不 settle：模拟慢请求，只观测 signal 状态。
          opts?.signal?.addEventListener('abort', () =>
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
          );
        }),
    );

    const { unmount, result } = renderHook(() => useProjectArtifacts('p1'));
    await waitFor(() => expect(fetchProjectArtifactsMock).toHaveBeenCalledTimes(1));
    expect(signal).toBeDefined();
    expect(signal?.aborted).toBe(false);
    expect(result.current.loading).toBe(true);

    unmount();

    expect(signal?.aborted).toBe(true);
    // 中止的请求不得把 error 写进状态（isAbortError 静默）
    expect(result.current.error).toBeNull();
  });

  it('切换项目时中止上一个项目的在途请求', async () => {
    const signals: Array<AbortSignal | undefined> = [];
    fetchProjectArtifactsMock.mockImplementation(
      (_projectId: string, opts?: { signal?: AbortSignal }) => {
        signals.push(opts?.signal);
        return new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () =>
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
          );
        });
      },
    );

    const { rerender } = renderHook(
      ({ projectId }: { projectId: string }) => useProjectArtifacts(projectId),
      { initialProps: { projectId: 'p1' } },
    );
    await waitFor(() => expect(fetchProjectArtifactsMock).toHaveBeenCalledTimes(1));

    rerender({ projectId: 'p2' });
    await waitFor(() => expect(fetchProjectArtifactsMock).toHaveBeenCalledTimes(2));

    expect(signals).toHaveLength(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });
});
