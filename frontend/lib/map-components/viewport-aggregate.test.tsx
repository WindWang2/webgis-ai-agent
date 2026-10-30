/**
 * useViewportAggregate（Track B · M7/M8）hook 契约：
 * - enabled/bbox 就绪才发请求（携带 groupBy/revision/bbox）；
 * - generation 变化作废 in-flight（连续手势不堆积、旧响应不落屏）；
 * - AggregateOverLimit → overlimit 态；其它错误 → error 态；
 * - 诚实元数据（scanned/matched/groups/truncated/null/nonNumeric/revision）。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { useViewportAggregate } from '@/lib/map-components/viewport-aggregate';
import { AggregateOverLimit, PagedRevisionConflict } from '@/lib/data-plane/paged-features';
import { setSessionIdentity } from '@/lib/store/session-identity';
import type { Layer } from '@/lib/types/layer';

vi.mock('@/lib/data-plane/paged-features', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/data-plane/paged-features')>();
  return {
    ...actual,
    fetchLayerAggregate: vi.fn(),
  };
});

import { fetchLayerAggregate } from '@/lib/data-plane/paged-features';

const mockAgg = vi.mocked(fetchLayerAggregate);

const MVT_LAYER = {
  id: 'mvt-layer',
  name: '学校',
  type: 'circle',
  visible: true,
  source: { type: 'vector' },
  _tileUrl: '/api/v1/layers/data/ref:schools/tiles/{z}/{x}/{y}.mvt?session_id=session-aggtest-0001',
  _refId: 'ref:schools',
  _descriptor: { mvt_capable: true, feature_count: 80000, content_revision: 4 },
} as unknown as Layer;

function aggResult(extra: Record<string, unknown> = {}) {
  return {
    rows: [
      { key: '小学', count: 120 },
      { key: '中学', count: 80 },
      { key: '__other__', count: 5 },
    ],
    groupCount: 12,
    isComplete: false,
    truncated: true,
    scanned: 80000,
    totalMatching: 205,
    nullKeyCount: 3,
    nonNumericCount: 0,
    revision: 4,
    reasonCodes: ['aggregate_full_scan', 'bbox_coarse', 'groups_truncated'],
    ...extra,
  };
}

function Harness(props: {
  enabled: boolean;
  layer: Layer | null;
  groupBy: string;
  bbox: [number, number, number, number] | null;
  generation: number;
}) {
  const agg = useViewportAggregate(props);
  return (
    <div>
      <div data-testid="agg-status">{agg.status}</div>
      <div data-testid="agg-rows">
        {agg.status === 'ready' ? agg.rows.map((r) => `${r.key}:${r.count}`).join(',') : ''}
      </div>
      <div data-testid="agg-meta">
        {agg.status === 'ready'
          ? `${agg.meta.scanned}/${agg.meta.totalMatching}/${agg.meta.groupCount}/${agg.meta.truncated}/${agg.meta.nullKeyCount}`
          : ''}
      </div>
    </div>
  );
}

describe('useViewportAggregate', () => {
  beforeEach(() => {
    mockAgg.mockReset();
    act(() => setSessionIdentity('session-aggtest-0001', null));
  });

  it('fetches on enabled+bbox with groupBy/revision and exposes honest meta', async () => {
    mockAgg.mockResolvedValueOnce(aggResult());
    render(<Harness enabled layer={MVT_LAYER} groupBy="类型" bbox={[104, 30, 105, 31]} generation={1} />);
    await waitFor(() => {
      expect(screen.getByTestId('agg-status').textContent).toBe('ready');
    });
    expect(mockAgg).toHaveBeenCalledTimes(1);
    expect(mockAgg.mock.calls[0][0]).toMatchObject({
      sessionId: 'session-aggtest-0001',
      refId: 'ref:schools',
      groupBy: '类型',
      revision: 4,
    });
    expect((mockAgg.mock.calls[0][0].bbox as number[]).join(',')).toBe('104,30,105,31');
    expect(screen.getByTestId('agg-rows').textContent).toContain('小学:120');
    expect(screen.getByTestId('agg-meta').textContent).toBe('80000/205/12/true/3');
  });

  it('generation change cancels in-flight; stale response never lands', async () => {
    const responders: Array<() => Promise<unknown>> = [
      () => new Promise(() => { /* 永不 resolve：模拟慢请求 */ }),
      () => Promise.resolve(aggResult({ rows: [{ key: '新', count: 1 }], truncated: false, groupCount: 1 })),
    ];
    mockAgg.mockImplementation(() => {
      const next = responders.shift();
      return next ? next() : Promise.resolve(aggResult());
    });
    const { rerender } = render(
      <Harness enabled layer={MVT_LAYER} groupBy="类型" bbox={[104, 30, 105, 31]} generation={1} />,
    );
    await waitFor(() => {
      expect(mockAgg).toHaveBeenCalledTimes(1);
    });
    rerender(
      <Harness enabled layer={MVT_LAYER} groupBy="类型" bbox={[104.5, 30, 105, 31]} generation={2} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('agg-status').textContent).toBe('ready');
    });
    expect(screen.getByTestId('agg-rows').textContent).toBe('新:1');
    expect(mockAgg).toHaveBeenCalledTimes(2);
  });

  it('AggregateOverLimit → overlimit (honest, no fake data)', async () => {
    mockAgg.mockRejectedValueOnce(new AggregateOverLimit());
    render(<Harness enabled layer={MVT_LAYER} groupBy="类型" bbox={[104, 30, 105, 31]} generation={1} />);
    await waitFor(() => {
      expect(screen.getByTestId('agg-status').textContent).toBe('overlimit');
    });
  });

  it('disabled or missing bbox stays idle (no request)', () => {
    const { rerender } = render(<Harness enabled={false} layer={MVT_LAYER} groupBy="类型" bbox={[1, 2, 3, 4]} generation={1} />);
    rerender(<Harness enabled layer={MVT_LAYER} groupBy="类型" bbox={null} generation={1} />);
    expect(mockAgg).not.toHaveBeenCalled();
    expect(screen.getByTestId('agg-status').textContent).toBe('idle');
  });

  it('R4: 409 conflict re-anchors once with server revision, then recovers', async () => {
    const responders: Array<() => Promise<unknown>> = [
      () => Promise.reject(new PagedRevisionConflict(7)),
      () => Promise.resolve(aggResult({ revision: 7 })),
    ];
    mockAgg.mockImplementation(() => {
      const next = responders.shift();
      return next ? next() : Promise.resolve(aggResult());
    });
    render(<Harness enabled layer={MVT_LAYER} groupBy="类型" bbox={[104, 30, 105, 31]} generation={1} />);
    // 第一次 409 → 自动以 revision=7 重锚重试 → 恢复 ready
    await waitFor(() => {
      expect(screen.getByTestId('agg-status').textContent).toBe('ready');
    }, { timeout: 3000 });
    expect(mockAgg).toHaveBeenCalledTimes(2);
    expect(mockAgg.mock.calls[1][0].revision).toBe(7);
  });
});
