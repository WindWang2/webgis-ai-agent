import type { Layer } from "@/lib/types/layer";
import type { MapSpec, MapSpecLayer } from "@/lib/mapspec-compiler/types";
import { SUBLAYER_SEP } from "./adapter";

function equalStructured(left: unknown, right: unknown): boolean {
  if (typeof left === "number" && typeof right === "number") {
    return Number.isFinite(left) && Number.isFinite(right) && Math.abs(left - right) <= 1e-9;
  }
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => equalStructured(value, right[index]));
  }
  if (left && right && typeof left === "object" && typeof right === "object") {
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const leftKeys = Object.keys(leftRecord).sort();
    const rightKeys = Object.keys(rightRecord).sort();
    return equalStructured(leftKeys, rightKeys)
      && leftKeys.every((key) => equalStructured(leftRecord[key], rightRecord[key]));
  }
  return false;
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
  if (
    !actual
    || String(actual.source ?? "") !== String(expected.source ?? "")
    || String(actual.type ?? "") !== String(expected.type ?? "")
  ) {
    return false;
  }
  const expectedRecord = expected as unknown as Record<string, unknown>;
  const actualRecord = actual as Record<string, unknown>;
  for (const key of ["filter", "source-layer", "minzoom", "maxzoom"] as const) {
    if (
      expectedRecord[key] !== undefined
      && !equalStructured(actualRecord[key], expectedRecord[key])
    ) {
      return false;
    }
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

function liveConstantOpacity(
  map: any,
  expectedLayers: Array<{ candidate: MapSpecLayer; actual: any }>,
): number | undefined {
  const values: number[] = [];
  for (const { candidate, actual } of expectedLayers) {
    if (!actual) return undefined;
    const opacityKeys = Object.keys(candidate.paint ?? {}).filter(
      (key) => key.endsWith("-opacity"),
    );
    for (const key of opacityKeys) {
      const value = liveProperty(map, actual, candidate.id, "paint", key);
      if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
      values.push(value);
    }
  }
  if (values.length === 0) return undefined;
  return values.every((value) => Math.abs(value - values[0]) <= 1e-9)
    ? values[0]
    : undefined;
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
  applied: MapSpec | null = null,
): Record<string, unknown> {
  const styleLoaded = !!map?.isStyleLoaded?.();
  const layers = hudLayers.map((hud) => {
    const attested = hud._mapspecFingerprint === mapspecFingerprint;
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
    const sourceConverged = expected.length > 0 && expected.every((candidate) => {
      const desiredSource = desired.sources[candidate.source];
      const appliedSource = applied?.sources[candidate.source];
      const liveSource = map.getSource?.(candidate.source);
      const liveStyleSource = map.getStyle?.()?.sources?.[candidate.source];
      const liveType = liveStyleSource?.type ?? liveSource?.type;
      const expectedLiveType = desiredSource?.type === "raster"
        ? "image"
        : desiredSource?.type;
      return (
        !!desiredSource
        && appliedSource === desiredSource
        && !!liveSource
        && liveType === expectedLiveType
      );
    });
    const styleConverged = (
      attested
      && styleLoaded
      && !reconcileError
      && sourceConverged
      && expected.length === live.length
      && expected.every((candidate) => layerConverged(map, candidate))
    );
    const rasterSource = (
      hud.source
      && typeof hud.source === "object"
      && "image" in hud.source
      && "bbox" in hud.source
    ) ? hud.source as { image: string; bbox: [number, number, number, number] } : null;
    return {
      id: hud._mapspecLayerId ?? hud.id,
      runtime_store_id: hud.id,
      name: hud.name,
      type: hud.type,
      group: hud.group,
      _refId: hud._refId,
      _descriptor: hud._descriptor,
      visible: visibility.length > 0 && visibility.every(Boolean),
      // Runtime quality evidence must come from MapLibre, not the potentially
      // stale HUD projection. Divergent/expression opacities remain unevaluated.
      opacity: liveConstantOpacity(map, liveByExpected),
      style: hud.style,
      legend_spec: hud.legend_spec,
      // A user presentation edit clears the server generation attestation in
      // the store. Never reuse the old projection digest for that new state.
      projection_fingerprint: attested ? hud._mapspecProjectionFingerprint : undefined,
      repair_action_id: hud._mapspecRepairActionId,
      intent_generation: hud._intentGeneration,
      raster_image: rasterSource?.image,
      raster_bbox: rasterSource?.bbox,
      source_converged: sourceConverged,
      style_converged: styleConverged,
      generation_attested: attested,
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
