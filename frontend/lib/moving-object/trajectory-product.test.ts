import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import {
  defaultTrajectoryConfig,
  findAppliedTrajectory,
  guessTrajectoryFieldBindings,
  inspectTrajectorySource,
  buildTrajectoryProduct,
  queryTrajectoryProduct,
  listTrajectorySourceCandidates,
  trajectoryConfigFromApplied,
  trajectoryStatsView,
} from './trajectory-product';

vi.mock('@/lib/api/transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/transport')>();
  return { ...actual, apiFetch: vi.fn() };
});

import { apiFetch } from '@/lib/api/transport';
const apiFetchMock = vi.mocked(apiFetch);

const SPEC = {
  version: '2.3',
  sources: {
    'bus-points': { type: 'geojson', url: 'ref:geojson-bus-1' },
    'inline-poi': { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } },
    'basemap': { type: 'raster', tiles: ['http://x/{z}/{x}/{y}.png'] },
    'traj-traj_x-lines-source': { type: 'geojson', url: 'ref:geojson-traj-lines' },
  },
  layers: [
    {
      id: 'traj-traj_x-lines',
      type: 'line',
      source: 'traj-traj_x-lines-source',
      trajectory: {
        schema_version: 'moving_object_trajectory.v1',
        product_ref: 'ref:traj-product-abc',
        family_id: 'traj_x',
        source_ref: 'ref:geojson-bus-1',
        source_revision: 3,
        source_fingerprint: 'sha256:a',
        product_fingerprint: 'sha256:b',
        stats: { entities: 3, segments: 9 },
        encoding: { width_min_px: 2, width_max_px: 9, show_direction: false },
        coverage: { assumed_timezone: 'Asia/Shanghai' },
        assumptions: [],
      },
    },
  ],
} as unknown as MapSpec;

describe('listTrajectorySourceCandidates', () => {
  it('offers geojson ref sources and excludes trajectory-derived products and inline sources', () => {
    const candidates = listTrajectorySourceCandidates(SPEC);
    expect(candidates.map((c) => c.key)).toEqual(['bus-points']);
    expect(candidates[0].ref).toBe('ref:geojson-bus-1');
  });

  it('returns empty for null/undefined specs', () => {
    expect(listTrajectorySourceCandidates(null)).toEqual([]);
    expect(listTrajectorySourceCandidates(undefined)).toEqual([]);
  });
});

describe('findAppliedTrajectory', () => {
  it('finds the first layer carrying a trajectory contract block', () => {
    const applied = findAppliedTrajectory(SPEC);
    expect(applied?.layerId).toBe('traj-traj_x-lines');
    expect(applied?.trajectory.family_id).toBe('traj_x');
  });

  it('returns null when nothing applied', () => {
    expect(findAppliedTrajectory({ version: '2.3', sources: {}, layers: [] })).toBeNull();
  });
});

describe('trajectoryConfigFromApplied', () => {
  it('restores editable config with defaults for absent keys (no fabrication)', () => {
    const applied = findAppliedTrajectory(SPEC);
    const config = trajectoryConfigFromApplied(applied!.trajectory);
    expect(config.dataset).toBe('ref:geojson-bus-1');
    expect(config.assumed_timezone).toBe('Asia/Shanghai');
    expect(config.width_min_px).toBe(2);
    expect(config.width_max_px).toBe(9);
    expect(config.show_direction).toBe(false);
    // untouched keys keep the honest defaults
    expect(config.boundary_rule).toBe('closed');
    expect(config.max_gap_s).toBe(3600);
  });
});

describe('guessTrajectoryFieldBindings', () => {
  it('prefers exact names (case-insensitive)', () => {
    const g = guessTrajectoryFieldBindings({
      'Vehicle_ID': 'str', 'TIMESTAMP': 'str', 'other': 'num',
    });
    expect(g.entity_id_field).toBe('Vehicle_ID');
    expect(g.time_field).toBe('TIMESTAMP');
  });

  it('falls back to substring and then empty (never fabricated)', () => {
    const g = guessTrajectoryFieldBindings({ 'vehicle_num': 'str', 'obs_time': 'str' });
    expect(g.entity_id_field).toBe('vehicle_num');
    expect(g.time_field).toBe('obs_time');
    const none = guessTrajectoryFieldBindings({ 'foo': 'str' });
    expect(none.entity_id_field).toBe('');
    expect(none.time_field).toBe('');
  });
});

describe('trajectoryStatsView', () => {
  it('projects bounded numbers from real stats with null-safe km', () => {
    const view = trajectoryStatsView({ entities: 3, observations: 120, segments: 3, dwells: 2, breaks_total: 1, total_path_length_m: 12345.6 });
    expect(view).toEqual({ entities: 3, observations: 120, segments: 3, dwells: 2, breaks: 1, totalKm: 12.3, warningCount: 0 });
    const empty = trajectoryStatsView(undefined);
    expect(empty.entities).toBe(0);
    expect(empty.totalKm).toBeNull();
  });
});

describe('API boundary paths', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({} as never);
  });

  it('posts inspect to the trajectory inspect route', async () => {
    await inspectTrajectorySource('s1', {
      dataset: 'ref:geojson-bus-1', entity_id_field: 'entity_id',
      time_field: 'timestamp', assumed_timezone: 'UTC',
    });
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/v1/chat/sessions/s1/trajectory/inspect',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('posts build config to the trajectory build route', async () => {
    await buildTrajectoryProduct('s1', defaultTrajectoryConfig({ dataset: 'ref:geojson-bus-1' }));
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/chat/sessions/s1/trajectory/build');
    expect((init as { body: Record<string, unknown> }).body.dataset).toBe('ref:geojson-bus-1');
  });

  it('posts bounded query pages', async () => {
    await queryTrajectoryProduct('s1', { product_ref: 'ref:traj-product-abc', kind: 'dwell', entity_id: 'BUS-01', offset: 10, limit: 25 });
    const [, init] = apiFetchMock.mock.calls[0];
    expect((init as { body: Record<string, unknown> }).body).toMatchObject({
      product_ref: 'ref:traj-product-abc', kind: 'dwell',
      entity_id: 'BUS-01', offset: 10, limit: 25,
    });
  });
});
