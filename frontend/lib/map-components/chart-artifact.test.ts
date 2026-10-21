import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/**
 * #1746-4：chart artifact 失败条目的 TTL 重试窗口必须在异步拉取路径上生效。
 *
 * loadChartArtifact 此前读原始 `cache.get(key)`，失败条目（null）因此在会话内
 * 永久驻留 —— #1078(G-4) 承诺的「TTL 后可重试」在异步路径上永不生效，瞬时
 * 500/超时让降级卡片无法恢复。现在统一走 TTL 感知的 getCachedChartArtifact。
 */

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api/transport', () => ({ apiFetch: apiFetchMock }));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'sid-chart', revision: 1, ownerToken: null }),
}));

import {
  getCachedChartArtifact,
  loadChartArtifact,
  resetChartArtifactCache,
} from './chart-artifact';

const CHART = {
  type: 'bar' as const,
  title: 'gdp',
  data: [{ name: 'a', value: 1 }],
};

describe('chart artifact 失败 TTL', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-20T00:00:00Z'));
    apiFetchMock.mockReset();
    resetChartArtifactCache();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('失败后在 TTL 窗口内复用 null，过期后重新拉取', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('500 transient'));

    // 第一次：拉取失败 → null（且不抛错）
    await expect(loadChartArtifact('ref:chart-1')).resolves.toBeNull();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);

    // TTL 内（+29s）：同步读视为「已拉取（失败）」→ 不再发请求
    vi.setSystemTime(new Date('2026-10-20T00:00:29Z'));
    expect(getCachedChartArtifact('ref:chart-1')).toBeNull();
    await expect(loadChartArtifact('ref:chart-1')).resolves.toBeNull();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);

    // 超过 FAILURE_TTL_MS（+31s）：失败条目视为未拉取 → 重新拉取并恢复。
    // 注意：这里必须先直接调 loadChartArtifact —— 若先调 getCachedChartArtifact，
    // 它自身的过期清理会把 bug（读原始 cache.get）掩盖掉。
    vi.setSystemTime(new Date('2026-10-20T00:00:31Z'));
    apiFetchMock.mockResolvedValueOnce({ chart: CHART });
    await expect(loadChartArtifact('ref:chart-1')).resolves.toEqual(CHART);
    expect(apiFetchMock).toHaveBeenCalledTimes(2);

    // 成功后是持久命中
    vi.setSystemTime(new Date('2026-10-20T01:00:00Z'));
    expect(getCachedChartArtifact('ref:chart-1')).toEqual(CHART);
  });

  it('成功条目不受 TTL 影响（不会被提前判为未拉取）', async () => {
    apiFetchMock.mockResolvedValueOnce({ chart: CHART });
    await expect(loadChartArtifact('ref:chart-2')).resolves.toEqual(CHART);
    vi.setSystemTime(new Date('2026-10-20T01:00:00Z'));
    expect(getCachedChartArtifact('ref:chart-2')).toEqual(CHART);
    await expect(loadChartArtifact('ref:chart-2')).resolves.toEqual(CHART);
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
  });

  it('getCachedChartArtifact：失败条目超过 TTL 视为未拉取（可重试）', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('500 transient'));
    await expect(loadChartArtifact('ref:chart-3')).resolves.toBeNull();

    vi.setSystemTime(new Date('2026-10-20T00:00:10Z'));
    expect(getCachedChartArtifact('ref:chart-3')).toBeNull();
    vi.setSystemTime(new Date('2026-10-20T00:00:31Z'));
    expect(getCachedChartArtifact('ref:chart-3')).toBeUndefined();
  });
});
