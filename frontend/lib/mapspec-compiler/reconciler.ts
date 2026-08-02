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
 * Normalize a value for deep equality. `JSON.stringify` is cheap and
 * unambiguous for the plain-data shapes MapSpec uses (no functions, Dates, or
 * circular refs). Object key insertion order is stable here because we always
 * build these objects from typed literals, not from arbitrary user input.
 */
function signature(val: unknown): string {
  return JSON.stringify(val);
}

function diffView(prev: MapSpecView | undefined, next: MapSpecView | undefined): ViewChange | undefined {
  if (signature(prev) === signature(next)) return undefined;
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
export function diffSpecs(prev: MapSpec | null, next: MapSpec): SpecPatch {
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
    } else if (signature(prevSources[id]) !== signature(nextSources[id])) {
      sources.push({ id, kind: "update", next: nextSources[id] });
    }
  }

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
    } else if (signature(prevLayer) !== signature(layer)) {
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
