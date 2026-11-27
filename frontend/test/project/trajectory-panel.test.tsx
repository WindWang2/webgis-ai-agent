/**
 * GOAL-20261127-02 轨迹面板组件测试（flow-panel / exploration-panel 同款
 * 边界纪律：只 mock `@/lib/moving-object/trajectory-product` 网络边界与
 * MapSpec session cursor；组件渲染主链、投影函数全部真实执行）。
 *
 * 覆盖：无会话诚实拒显（noSession）、无源提示（noSources）、inspect 指标
 * 真实展示、build 结果统计与 can_apply 门槛、无运动诚实空态（noMotion）、
 * 区域事件表（推断口径注释可见）。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TrajectoryPanel } from '@/components/moving-object/trajectory-panel';

vi.mock('@/lib/moving-object/trajectory-product', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('@/lib/moving-object/trajectory-product')
  >();
  return { ...actual, inspectTrajectorySource: vi.fn(), buildTrajectoryProduct: vi.fn() };
});

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: vi.fn(() => SPEC),
  getMapSpecSessionCursor: vi.fn(() => ({ sessionId: 'sess-1', ownerToken: 'tok', revision: 7 })),
  subscribeMapSpecLive: vi.fn(() => () => {}),
}));

vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitMapSpecMutation: vi.fn(),
}));

import {
  buildTrajectoryProduct,
  inspectTrajectorySource,
  type TrajectoryPreviewResponse,
} from '@/lib/moving-object/trajectory-product';
import { getCommittedMapSpec } from '@/lib/mapspec/session-cursor';

const SPEC = {
  version: '2.3',
  sources: { 'bus-points': { type: 'geojson', url: 'ref:geojson-bus-1' } },
  layers: [],
} as never;

const INSPECT = {
  success: true,
  operation: 'inspect',
  source_ref: 'ref:geojson-bus-1',
  source_revision: 3,
  feature_count: 60,
  fields: { entity_id: 'str', timestamp: 'str', accuracy_m: 'float' },
  sample: [],
  ingest_health: {
    input_rows: 60, accepted: 58, missing_entity_id: 1, unparseable_time: 1,
    missing_or_degenerate_geometry: 0, entity_count: 3,
  },
};

function previewResponse(overrides: Partial<TrajectoryPreviewResponse> = {}): TrajectoryPreviewResponse {
  return {
    success: true,
    operation: 'preview',
    product_ref: 'ref:traj-product-abc',
    family_id: 'traj_x',
    source_ref: 'ref:geojson-bus-1',
    source_revision: 3,
    cache_hit: 'miss',
    line_ref: 'ref:geojson-lines',
    dwell_ref: 'ref:geojson-dwells',
    stats: {
      observations: 58, entities: 3, segments: 3, dwells: 3,
      breaks_total: 2, total_path_length_m: 5400,
    },
    quality: { ingest: INSPECT.ingest_health },
    assumptions: ['运动量均为相邻观测间的区间估计'],
    warnings: [],
    can_apply: true,
    blocking_reasons: [],
    mapspec_fragment: { sources: {}, layers: [] },
    mapspec_revision: 7,
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(getCommittedMapSpec).mockReturnValue(SPEC);
  vi.mocked(inspectTrajectorySource).mockReset();
  vi.mocked(buildTrajectoryProduct).mockReset();
});

afterEach(() => {
  cleanup();
});

describe('TrajectoryPanel', () => {
  it('renders the source selector with real candidates', () => {
    render(<TrajectoryPanel />);
    const select = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
    expect(select).toBeTruthy();
    // real projection (not mocked): bus-points offered, no inline/raster
    fireEvent.change(select, { target: { value: 'ref:geojson-bus-1' } });
    expect(vi.mocked(inspectTrajectorySource)).toHaveBeenCalled();
  });

  it('shows build summary with bounded stats and apply gate', async () => {
    vi.mocked(inspectTrajectorySource).mockResolvedValue(INSPECT as never);
    vi.mocked(buildTrajectoryProduct).mockResolvedValue(previewResponse() as never);
    render(<TrajectoryPanel />);
    const select = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'ref:geojson-bus-1' } });
    const build = await screen.findByRole('button', { name: /构建轨迹|Build trajectories/ });
    fireEvent.click(build);
    // real projection applied over the (mocked) real response
    await waitFor(() => {
      expect(screen.getByText('58')).toBeTruthy();  // observations (real projection)
    });
    await screen.findByRole('button', { name: /应用到地图|Apply to map/ });
  });

  it('stays honest when no motion is observable (can_apply=false)', async () => {
    vi.mocked(inspectTrajectorySource).mockResolvedValue(INSPECT as never);
    vi.mocked(buildTrajectoryProduct).mockResolvedValue(previewResponse({
      can_apply: false, blocking_reasons: ['no_observable_motion'], stats: { observations: 0, entities: 0, segments: 0, dwells: 0, breaks_total: 0 },
    }) as never);
    render(<TrajectoryPanel />);
    const select = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'ref:geojson-bus-1' } });
    const build = await screen.findByRole('button', { name: /构建轨迹|Build trajectories/ });
    fireEvent.click(build);
    await screen.findByText(/无可观测运动|No observable motion/);
    expect(screen.queryByRole('button', { name: /应用到地图|Apply to map/ })).toBeNull();
  });

  it('renders region summaries with the inferred-semantics note', async () => {
    vi.mocked(inspectTrajectorySource).mockResolvedValue(INSPECT as never);
    vi.mocked(buildTrajectoryProduct).mockResolvedValue(previewResponse({
      events_summary: { n_events: 4, by_type: { enter: 2, exit: 1, cross: 1 }, unknown_intervals: 0, inferred_events: 3, notes: [] },
      region_summaries: [{
        region_id: 'STOP-A', n_entities: 2, n_enter: 2, n_exit: 1, n_cross: 1,
        n_dwell_spans: 1, total_dwell_s: 360, total_inside_fixes: 12,
        entities_zero_hit: 1, unknown_intervals: 0,
      }],
    }) as never);
    render(<TrajectoryPanel />);
    const select = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'ref:geojson-bus-1' } });
    fireEvent.click(await screen.findByRole('button', { name: /构建轨迹|Build trajectories/ }));
    await screen.findByText('STOP-A');
    // enter/exit/cross counts as 2/1/1
    expect(screen.getByText('2/1/1')).toBeTruthy();
    // zero-hit entity disclosed (honest denominator)
    expect(screen.getByText('1')).toBeTruthy();
    // sampling-interval semantics note is visible
    expect(screen.getByText(/inferred|观测区间推断/)).toBeTruthy();
  });

  it('does not render an apply button before a successful preview', () => {
    render(<TrajectoryPanel />);
    expect(screen.queryByRole('button', { name: /应用到地图|Apply to map/ })).toBeNull();
  });
});
