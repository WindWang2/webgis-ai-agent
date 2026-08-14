import type { MapSpec } from "@/lib/mapspec-compiler/types";
import { SUBLAYER_SEP } from "@/lib/mapspec-runtime/adapter";

/**
 * Interactive layer id helpers (Harness–Map Interaction V3, FE-3).
 *
 * The MapSpecRuntime owns the MapLibre id scheme: every project layer fans out
 * into sublayers keyed `${layerId}${SUBLAYER_SEP}${sub}`. Interactive affordances
 * (pointer cursor, click queries, hover tooltip) must enumerate those sublayer
 * ids. The runtime's applied spec is the authoritative registry of what the map
 * currently reflects (it only advances after a patch's ops have executed), so
 * we derive ids from it and only fall back to scanning the live style while the
 * runtime is missing or its first patch is still in flight.
 */

/**
 * Derive interactive sublayer ids from the runtime's applied spec, falling back
 * to a live style scan when `appliedSpec` is null (runtime not created yet, or
 * the first patch has not finished applying).
 */
export function computeInteractiveIds(
  appliedSpec: MapSpec | null,
  styleLayers: ReadonlyArray<{ id: string }>,
): string[] {
  if (appliedSpec) {
    return appliedSpec.layers
      .map((l) => l.id)
      .filter((id) => id.includes(SUBLAYER_SEP));
  }
  return styleLayers
    .map((l) => l.id)
    .filter((id) => id.includes(SUBLAYER_SEP));
}

/**
 * Resolve a sublayer id (`${layerId}__${sub}`) back to its PARENT project layer
 * id via LONGEST-prefix match against the known project layer ids.
 *
 * Why longest-prefix: layers `poi` and `poi_schools` both prefix-match the
 * sublayer `poi_schools__point`; a naive first-match attributes the click to
 * whichever layer comes first in the array (mis-attribution when `poi` sorts
 * ahead of `poi_schools`). Matching on `id + SUBLAYER_SEP` and picking the
 * longest candidate always lands on the true parent.
 *
 * Returns undefined when no project layer owns the sublayer (e.g. `process-*`
 * overlay layers) — callers fall back to the raw sublayer id.
 */
export function resolveParentLayerId(
  sublayerId: string,
  layerIds: ReadonlyArray<string> | ReadonlySet<string>,
): string | undefined {
  const isSet = typeof (layerIds as any).has === 'function';
  const sepIdx = sublayerId.lastIndexOf(SUBLAYER_SEP);

  if (isSet) {
    const set = layerIds as ReadonlySet<string>;
    if (sepIdx > 0) {
      const candidate = sublayerId.slice(0, sepIdx);
      if (set.has(candidate)) return candidate;
    }
    let best: string | undefined;
    set.forEach((id) => {
      if (sublayerId.startsWith(id + SUBLAYER_SEP)) {
        if (best === undefined || id.length > best.length) best = id;
      }
    });
    return best;
  }

  const arr = layerIds as ReadonlyArray<string>;
  if (sepIdx > 0) {
    const candidate = sublayerId.slice(0, sepIdx);
    if (arr.indexOf(candidate) !== -1) {
      let hasLonger = false;
      for (let i = 0; i < arr.length; i++) {
        const id = arr[i];
        if (id.length > candidate.length && sublayerId.startsWith(id + SUBLAYER_SEP)) {
          hasLonger = true;
          break;
        }
      }
      if (!hasLonger) return candidate;
    }
  }

  let best: string | undefined;
  for (let i = 0; i < arr.length; i++) {
    const id = arr[i];
    if (sublayerId.startsWith(id + SUBLAYER_SEP)) {
      if (best === undefined || id.length > best.length) best = id;
    }
  }
  return best;
}
