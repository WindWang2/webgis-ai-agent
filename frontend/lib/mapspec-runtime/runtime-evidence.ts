import type { Layer } from "@/lib/types/layer";
import type { MapSpec, MapSpecLayer } from "@/lib/mapspec-compiler/types";
import { SUBLAYER_SEP } from "./adapter";

function equalStructured(left: unknown, right: unknown): boolean {
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function liveProperty(
  map: any,
  layer: any,
  layerId: string,
  group: "paint" | "layout",
  key: string,
): unknown {
  const getter = group === "paint" ? map.getPaintProperty : map.getLayoutProperty;
  if (typeof getter === "function") {
    try {
      return getter.call(map, layerId, key);
    } catch {
      return undefined;
    }
  }
  return layer?.[group]?.[key];
}

function layerConverged(map: any, expected: MapSpecLayer): boolean {
  const actual = map.getLayer?.(expected.id);
  if (!actual || String(actual.source ?? "") !== String(expected.source ?? "")) {
    return false;
  }
  for (const [key, value] of Object.entries(expected.paint ?? {})) {
    if (!equalStructured(liveProperty(map, actual, expected.id, "paint", key), value)) {
      return false;
    }
  }
  for (const [key, value] of Object.entries(expected.layout ?? {})) {
    if (!equalStructured(liveProperty(map, actual, expected.id, "layout", key), value)) {
      return false;
    }
  }
  return true;
}

function viewportOf(map: any): Record<string, unknown> {
  const center = map.getCenter?.();
  const bounds = map.getBounds?.();
  return {
    center: center && Number.isFinite(center.lng) && Number.isFinite(center.lat)
      ? [center.lng, center.lat]
      : undefined,
    zoom: map.getZoom?.(),
    bearing: map.getBearing?.(),
    pitch: map.getPitch?.(),
    bounds: bounds && typeof bounds.getWest === "function"
      ? [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]
      : undefined,
  };
}

/**
 * Compare the locally derived desired runtime spec with the live MapLibre map.
 * The result contains bounded metadata only: no source bodies or features.
 */
export function collectCartographicRuntimeObservation(
  map: any,
  desired: MapSpec,
  hudLayers: Layer[],
  mapspecFingerprint: string,
  reconcileError = "",
): Record<string, unknown> {
  const styleLoaded = !!map?.isStyleLoaded?.();
  const layers = hudLayers.map((hud) => {
    const expected = desired.layers.filter(
      (candidate) => candidate.id.startsWith(`${hud.id}${SUBLAYER_SEP}`),
    );
    const liveByExpected = expected.map(
      (candidate) => ({ candidate, actual: map.getLayer?.(candidate.id) }),
    );
    const live = liveByExpected.filter(({ actual }) => !!actual);
    const visibility = liveByExpected.map(({ candidate, actual }) => (
      !!actual
      && liveProperty(map, actual, candidate.id, "layout", "visibility") !== "none"
    ));
    const sourceConverged = expected.length > 0 && expected.every(
      (candidate) => !!map.getSource?.(candidate.source),
    );
    const styleConverged = (
      styleLoaded
      && !reconcileError
      && sourceConverged
      && expected.length === live.length
      && expected.every((candidate) => layerConverged(map, candidate))
    );
    return {
      id: hud._mapspecLayerId ?? hud.id,
      _refId: hud._refId,
      _descriptor: hud._descriptor,
      visible: visibility.length > 0 && visibility.every(Boolean),
      opacity: hud.opacity,
      legend_spec: hud.legend_spec,
      source_converged: sourceConverged,
      style_converged: styleConverged,
      runtime_layer_count: live.length,
      runtime_layer_ids: expected.map((candidate) => candidate.id).slice(0, 16),
    };
  });
  return {
    mapspec_fingerprint: mapspecFingerprint,
    layers,
    viewport: viewportOf(map),
    style_loaded: styleLoaded,
    reconcile_error: reconcileError.slice(0, 500),
  };
}
