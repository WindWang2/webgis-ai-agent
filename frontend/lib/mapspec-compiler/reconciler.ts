import { MapSpec, MapSpecLayer, MapSpecSource, MapSpecView } from "./types";

/**
 * SpecPatch — the output of `diffSpecs`. Describes the delta between two
 * MapSpecs as a set of source/layer/view changes.
 *
 * Design note (ADR-0036, Q4): the patch reports *which* layers/sources changed
 * and *how* (add/remove/recompile/update). It is pure — it carries no MapLibre
 * knowledge. The side-effectful `MapSpecRuntime` decides how to apply each
 * change to a live map instance. Keeping the diff pure lets it live in this
 * package (alongside the compiler) and be unit-tested without a map.
 */

export interface SourceChange {
  id: string;
  /** `add` = new source; `update` = source id kept but payload changed (e.g. new geojson data ref or raster url); `remove` = gone */
  kind: "add" | "remove" | "update";
  /** Present for `add`/`update`. The next source definition to apply. */
  next?: MapSpecSource;
}

export interface LayerChange {
  id: string;
  /**
   * `add` = new layer; `remove` = gone;
   * `recompile` = layer id kept but type/source/paint/layout/label changed.
   *
   * Per ADR-0036 Q3: rather than diffing individual paint properties, a changed
   * layer is reported as `recompile` and the runtime removes + re-adds it. This
   * keeps the pure diff simple and isolates the fiddly per-property expression
   * recompilation policy in the side-effectful layer.
   */
  kind: "add" | "remove" | "recompile";
  /** Present for `add`/`recompile`. The next layer definition. */
  next?: MapSpecLayer;
}

export interface ViewChange {
  prev?: MapSpecView;
  next?: MapSpecView;
}

export interface SpecPatch {
  sources: SourceChange[];
  layers: LayerChange[];
  /**
   * Present only when the view changed. Per ADR-0036 Q3 the runtime *ignores*
   * view changes (view stays imperative via `flyTo`/`easeTo`). It is included
   * in the patch for completeness and so consumers/tests can observe it.
   */
  view?: ViewChange;
}

/**
 * Fast early-exit structural deep equality check for MapSpec nodes.
 * Avoids JSON.stringify allocation overhead on every reconciliation frame.
 */
function isDeepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;

  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (!isDeepEqual(a[i], b[i])) return false;
    }
    return true;
  }

  const objA = a as Record<string, unknown>;
  const objB = b as Record<string, unknown>;
  const keysA = Object.keys(objA);
  const keysB = Object.keys(objB);
  if (keysA.length !== keysB.length) return false;

  for (const key of keysA) {
    if (!Object.prototype.hasOwnProperty.call(objB, key)) return false;
    if (!isDeepEqual(objA[key], objB[key])) return false;
  }
  return true;
}

function diffView(prev: MapSpecView | undefined, next: MapSpecView | undefined): ViewChange | undefined {
  if (isDeepEqual(prev, next)) return undefined;
  return { prev, next };
}


/**
 * Compute the delta between two MapSpecs.
 *
 * - `prev === null` means "the runtime has applied nothing yet" → everything
 *   in `next` is reported as `add`.
 * - Identical specs yield an empty patch (the runtime's no-op fast path).
 * - Layer order is significant (it controls z-order). A reordering of the same
 *   layer set is reported as `recompile` for every layer whose position
 *   changed, which is what the runtime needs to re-apply z-ordering.
 */
function isMapSpecShallowEqual(prev: MapSpec | null, next: MapSpec): boolean {
  if (prev === next) return true;
  if (!prev || !next) return false;
  if (prev.version !== next.version) return false;
  if (prev.sources === next.sources && prev.layers === next.layers) return true;
  return false;
}

export function diffSpecs(prev: MapSpec | null, next: MapSpec): SpecPatch {
  if (isMapSpecShallowEqual(prev, next)) {
    return { sources: [], layers: [], view: diffView(prev?.view, next.view) };
  }

  if (prev === null) {
    return {
      sources: Object.entries(next.sources || {}).map(([id, source]) => ({
        id,
        kind: "add" as const,
        next: source,
      })),
      layers: (next.layers || []).map((layer) => ({ id: layer.id, kind: "add" as const, next: layer })),
      view: diffView(undefined, next.view),
    };
  }

  // Sources: keyed by id.
  const prevSources = prev.sources || {};
  const nextSources = next.sources || {};
  const sourceIds = Array.from(new Set(Object.keys(prevSources).concat(Object.keys(nextSources))));
  const sources: SourceChange[] = [];
  for (const id of sourceIds) {
    const inPrev = id in prevSources;
    const inNext = id in nextSources;
    if (inPrev && !inNext) {
      sources.push({ id, kind: "remove" });
    } else if (!inPrev && inNext) {
      sources.push({ id, kind: "add", next: nextSources[id] });
    } else if (!isDeepEqual(prevSources[id], nextSources[id])) {
      sources.push({ id, kind: "update", next: nextSources[id] });
    }
  }
  const changedSourceIds = new Set(
    sources
      .filter((change) => change.kind === "update")
      .map((change) => change.id),
  );

  // Layers: keyed by id. Order matters for z-order; we walk next.layers in
  // order and emit changes in that order so the runtime applies them
  // deterministically. Removes are appended at the end (order irrelevant).
  const prevLayerById = new Map<string, MapSpecLayer>();
  for (const l of prev.layers || []) prevLayerById.set(l.id, l);
  const nextLayerIds = new Set((next.layers || []).map((l) => l.id));

  const layers: LayerChange[] = [];
  for (const layer of next.layers || []) {
    const prevLayer = prevLayerById.get(layer.id);
    if (!prevLayer) {
      layers.push({ id: layer.id, kind: "add", next: layer });
    } else if (
      !isDeepEqual(prevLayer, layer)
      || changedSourceIds.has(layer.source)
    ) {
      // MapLibre source definitions are not uniformly mutable. Recompile all
      // dependent layers around a source replacement so a same-id vector,
      // raster, URL, or data-generation update cannot leave stale live data.
      layers.push({ id: layer.id, kind: "recompile", next: layer });
    }

    // else: unchanged → omitted (no-op)
  }
  // Removes: layers present in prev but absent from next.
  for (const layer of prev.layers || []) {
    if (!nextLayerIds.has(layer.id)) {
      layers.push({ id: layer.id, kind: "remove" });
    }
  }

  return {
    sources,
    layers,
    view: diffView(prev.view, next.view),
  };
}
