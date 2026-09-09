import { describe, expect, it, beforeEach } from 'vitest';
import {
  registerChartRenderState,
  unregisterChartRenderState,
  snapshotChartRenderStates,
  clearChartRenderStates,
} from '@/lib/map-components/chart-render-registry';

describe('chart-render-registry (V5 W5 rendered-state telemetry)', () => {
  beforeEach(() => clearChartRenderStates());

  it('publishes and snapshots rendered state', () => {
    registerChartRenderState('chart-1', { rendered: true, data_points: 12 });
    registerChartRenderState('chart-2', { rendered: false, data_points: 0 });
    const snap = snapshotChartRenderStates();
    expect(snap).toHaveLength(2);
    expect(snap.find((c) => c.id === 'chart-1')).toEqual({
      id: 'chart-1', rendered: true, data_points: 12,
    });
  });

  it('re-register overwrites (latest wins)', () => {
    registerChartRenderState('chart-1', { rendered: false, data_points: 0 });
    registerChartRenderState('chart-1', { rendered: true, data_points: 7 });
    const snap = snapshotChartRenderStates();
    expect(snap).toHaveLength(1);
    expect(snap[0]).toEqual({ id: 'chart-1', rendered: true, data_points: 7 });
  });

  it('unregister removes entry (panel unmount)', () => {
    registerChartRenderState('chart-1', { rendered: true, data_points: 3 });
    unregisterChartRenderState('chart-1');
    expect(snapshotChartRenderStates()).toHaveLength(0);
  });

  it('is bounded at 32 entries with FIFO eviction', () => {
    for (let i = 0; i < 40; i += 1) {
      registerChartRenderState(`chart-${i}`, { rendered: true, data_points: i });
    }
    const snap = snapshotChartRenderStates();
    expect(snap).toHaveLength(32);
    expect(snap.some((c) => c.id === 'chart-0')).toBe(false);
    expect(snap.some((c) => c.id === 'chart-39')).toBe(true);
  });

  it('normalizes junk data_points to 0 (server contract)', () => {
    registerChartRenderState('chart-junk', {
      rendered: true,
      data_points: Number.NaN,
    });
    expect(snapshotChartRenderStates()[0]).toEqual({
      id: 'chart-junk', rendered: true, data_points: 0,
    });
  });
});
