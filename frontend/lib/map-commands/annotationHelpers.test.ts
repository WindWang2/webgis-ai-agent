import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  ANNOTATION_SOURCE_ID,
  ensureAnnotationLayers,
  raiseAnnotationLayers,
  ANNOTATION_LAYER_IDS,
} from './annotationHelpers';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import { useHudStore } from '@/lib/store/useHudStore';

/**
 * #460: the imperative annotation stack (markers / measurements / labels) is
 * wiped by every basemap setStyle. #402 gave the selection highlight a
 * re-mount on style recovery; this suite pins the same contract for the
 * annotation stack: after a wipe, raising the stack must RE-CREATE the
 * source + all four layers with their data, not just re-order survivors.
 */

const markerFeature = {
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [116.4, 39.9] },
  properties: { label: '学校', color: '#ef4444' },
};

describe('raiseAnnotationLayers — basemap setStyle wipe (#460)', () => {
  let map: any;
  beforeEach(() => {
    map = makeMockMaplibreMap();
    useHudStore.getState().clearAnnotations?.();
    useHudStore.setState({ annotations: [] as any });
  });

  it('early-returns (no mount) when no annotations exist — fresh maps stay clean', () => {
    raiseAnnotationLayers(map);
    expect(map.getStyle().layers).toEqual([]);
    expect(map.getSource(ANNOTATION_SOURCE_ID)).toBeNull();
  });

  it('re-mounts the source + all 4 layers with their data after a full style wipe', () => {
    useHudStore.setState({ annotations: [markerFeature] as any });
    // Mounted once by MapActionHandler's refresh effect, then wiped by setStyle.
    ensureAnnotationLayers(map);
    for (const l of [...map._layers]) map.removeLayer(l.id);
    for (const id of Object.keys(map._sources)) map.removeSource(id);
    expect(map.getLayer(`${ANNOTATION_SOURCE_ID}-fill`)).toBeNull();

    // The post-reconcile raise must REMOUNT (mirror of #402's highlight fix),
    // not early-return on the all-four-gone post-wipe state.
    raiseAnnotationLayers(map);

    for (const id of ANNOTATION_LAYER_IDS) {
      expect(map.getLayer(id)).toBeTruthy();
    }
    expect(map.getSource(ANNOTATION_SOURCE_ID)).toBeTruthy();
    const dataCalls = map._calls.setData.filter(
      (c: { id: string }) => c.id === ANNOTATION_SOURCE_ID,
    );
    expect(dataCalls.length).toBeGreaterThan(0);
    expect(dataCalls[dataCalls.length - 1].data.features).toEqual([markerFeature]);
  });

  it('re-raises (moves to top) when the stack survives a reconcile', () => {
    useHudStore.setState({ annotations: [markerFeature] as any });
    ensureAnnotationLayers(map);
    // Spec-style layer stacked above the annotations by syncLayerZOrder.
    map.addLayer({ id: 'poi__point', type: 'circle', source: 'poi' });
    // Bottom-to-top re-raise order: fill, line, circle, label.
    raiseAnnotationLayers(map);
    const ids = map._layers.map((l: any) => l.id);
    expect(ids.indexOf('poi__point')).toBeLessThan(
      ids.indexOf(`${ANNOTATION_SOURCE_ID}-fill`),
    );
    expect(ids.indexOf(`${ANNOTATION_SOURCE_ID}-fill`)).toBeLessThan(
      ids.indexOf(`${ANNOTATION_SOURCE_ID}-label`),
    );
  });

  it('is a silent no-op for layer mutation failures mid-reconcile', () => {
    useHudStore.setState({ annotations: [markerFeature] as any });
    ensureAnnotationLayers(map);
    map.moveLayer = vi.fn(() => {
      throw new Error('layer vanished mid-reconcile');
    });
    expect(() => raiseAnnotationLayers(map)).not.toThrow();
  });

  // ─── style-load race: "Style is not done loading." ─────────────────────
  // The #460 remount runs right after a basemap setStyle, while the style is
  // mid-reload. Mounting then throws (MapLibre rejects style mutations before
  // the style is usable); the remount must defer until the style is ready.

  it('defers the post-wipe remount while the style is reloading, mounts on load', () => {
    useHudStore.setState({ annotations: [markerFeature] as any });
    const reloading = makeMockMaplibreMap({ styleLoaded: false });

    expect(() => raiseAnnotationLayers(reloading)).not.toThrow();
    expect(reloading.getSource(ANNOTATION_SOURCE_ID)).toBeNull();
    expect(reloading.getStyle().layers).toEqual([]);

    reloading._fire('load');

    for (const id of ANNOTATION_LAYER_IDS) {
      expect(reloading.getLayer(id)).toBeTruthy();
    }
    const dataCalls = reloading._calls.setData.filter(
      (c: { id: string }) => c.id === ANNOTATION_SOURCE_ID,
    );
    expect(dataCalls.length).toBeGreaterThan(0);
    expect(dataCalls[dataCalls.length - 1].data.features).toEqual([markerFeature]);
  });

  it('skips the deferred remount when another path mounted the stack meanwhile', () => {
    useHudStore.setState({ annotations: [markerFeature] as any });
    const reloading = makeMockMaplibreMap({ styleLoaded: false });

    raiseAnnotationLayers(reloading); // schedules the deferred remount
    // MapActionHandler's style-gated refresh effect wins the race and mounts
    // first (the mock, unlike real MapLibre, tolerates the pre-load window).
    ensureAnnotationLayers(reloading);
    const addSourceCallsBefore = reloading._calls.addSource.length;

    reloading._fire('load');

    expect(reloading._calls.addSource.length).toBe(addSourceCallsBefore); // no double mount
    expect(
      reloading._layers.filter((l: any) => l.id.startsWith(ANNOTATION_SOURCE_ID)),
    ).toHaveLength(4);
  });
});
