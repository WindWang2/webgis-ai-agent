import { describe, it, expect, vi } from 'vitest';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { annotationCommands } from './annotationCommands';
import { ensureAnnotationLayers, ANNOTATION_SOURCE_ID } from './annotationHelpers';
import type { MapCommandContext } from './types';

function makeCtx(map: any, params: Record<string, unknown> = {}): MapCommandContext {
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({
      addAnnotation: vi.fn(),
      clearAnnotations: vi.fn(),
    }),
    setSelectedBaseLayer: () => {},
    command: 'add_marker',
    params,
  } as MapCommandContext;
}

// ROUND-2: annotation commands only claim confirmed:true when the data actually
// reached the map's annotation source. When the source is absent,
// refreshAnnotations no-ops — the command must fail target_not_found instead of
// acking a success the map never earned.
describe('annotationCommands (ROUND-2: confirmed only when data reached the map)', () => {
  it('add_marker succeeds with confirmed:true when the annotation source exists', () => {
    const map = makeMockMaplibreMap();
    const result = annotationCommands.add_marker.run(
      makeCtx(map, { longitude: 116, latitude: 39, label: 'x' }),
    );

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    // ensureAnnotationLayers mounted the source; refreshAnnotations pushed data
    expect(map._calls.addSource).toHaveLength(1);
    expect(map._calls.setData).toHaveLength(1);
    expect(map._calls.setData[0].id).toBe(ANNOTATION_SOURCE_ID);
  });

  it('add_marker fails target_not_found when the annotation source is absent (refreshAnnotations no-ops)', () => {
    // A map that never registers added sources (getSource always null) — the
    // ensure→refresh path silently no-ops, so confirmed:true would be a fake ack.
    const bareMap = {
      getSource: vi.fn(() => null),
      getLayer: vi.fn(() => null),
      addSource: vi.fn(),
      addLayer: vi.fn(),
    };
    const result = annotationCommands.add_marker.run(
      makeCtx(bareMap, { longitude: 116, latitude: 39 }),
    );

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
  });

  it('draw_measurement succeeds with confirmed:true when the source exists', () => {
    const map = makeMockMaplibreMap();
    const result = annotationCommands.draw_measurement.run(
      makeCtx(map, { coordinates: [[116, 39], [117, 40]], label: 'd' }),
    );

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map._calls.setData).toHaveLength(1);
  });

  it('draw_measurement fails target_not_found when the source is absent', () => {
    const bareMap = {
      getSource: vi.fn(() => null),
      getLayer: vi.fn(() => null),
      addSource: vi.fn(),
      addLayer: vi.fn(),
    };
    const result = annotationCommands.draw_measurement.run(
      makeCtx(bareMap, { coordinates: [[116, 39], [117, 40]] }),
    );

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
  });

  it('clear_annotations succeeds with confirmed:true when the source exists', () => {
    const map = makeMockMaplibreMap();
    // clear_annotations does NOT mount the source itself (unlike add_marker /
    // draw_measurement) — in production the map-action-handler's
    // annotation-refresh effect (ensureAnnotationLayers) already mounted it.
    ensureAnnotationLayers(map);
    const result = annotationCommands.clear_annotations.run(makeCtx(map));

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map._calls.setData).toHaveLength(1);
  });

  it('clear_annotations fails target_not_found when the source is absent', () => {
    const bareMap = {
      getSource: vi.fn(() => null),
      getLayer: vi.fn(() => null),
      addSource: vi.fn(),
      addLayer: vi.fn(),
    };
    const result = annotationCommands.clear_annotations.run(makeCtx(bareMap));

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
  });
});
