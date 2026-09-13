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
   * `filter` = ONLY the filter expression changed (Runtime V4 fast path) —
   *   the runtime applies it via map.setFilter with zero layer churn.
   * `paint` / `layout` (AC-06 ADR-0155 P3) = only paint / only layout keys
   *   changed — the runtime applies them via setPaintProperty /
   *   setLayoutProperty with zero remove/add churn. When BOTH paint and
   *   layout changed, two entries are emitted (paint first, then layout).
   *   Anything touching type/source/sourceLayer/cluster/label/etc. stays
   *   `recompile` — those fields cannot be mutated in place safely.
   *
   * Per ADR-0036 Q3: rather than diffing individual paint properties, a changed
   * layer is reported as `recompile` and the runtime removes + re-adds it. This
   * keeps the pure diff simple and isolates the fiddly per-property expression
   * recompilation policy in the side-effectful layer.
   *
   * The `filter` kind is the ONE deliberate exception (ADR-0091): selection /
   * legend-range / imperative filter flips are the highest-frequency mutation
   * in the interactive workspace, and a filter-only change maps 1:1 onto
   * map.setFilter — no remove/add, no recompile flicker, source untouched.
   *
   * AC-06 extends the same rationale to paint (改色零闪烁) and layout: they
   * are the next-highest-frequency mutations (legend interactions, style
   * tweaks) and map 1:1 onto MapLibre's property setters. The runtime keeps
   * recompile as the FALLBACK whenever an in-place patch fails.
   */
  kind: "add" | "remove" | "recompile" | "filter" | "paint" | "layout";
  /** Present for `add`/`recompile`/`paint`/`layout`/`filter`. The next layer definition. */
  next?: MapSpecLayer;
  /** Present for `paint`: the canonical paint keys that changed (informational). */
  paintKeys?: string[];
  /** Present for `layout`: the layout keys that changed (informational). */
  layoutKeys?: string[];
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
 * (AC-06 P5: exported — the runtime reuses it for native-paint dict diffing.)
 */
export function isDeepEqual(a: unknown, b: unknown): boolean {
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

// ── AC-06 P5：inline GeoJSON 差分短路 ───────────────────────────────────────
// 源对象身份不同但内容相同的重建（recompose）此前触发全量逐坐标走查
// （10k 要素 ≈ 6-10ms/次，见 ac-06-runtime-recon.md 基线）。两条短路：
//  1. content_revision 相等（数据版本相同）→ 除 inlineData 外浅深比较；
//  2. inlineData 指纹（WeakMap 按对象身份缓存，FNV-1a over JSON）快败 ——
//     指纹不同必不相等，跳过走查；指纹相等仍走全量比较保证正确性
//     （指纹只作「不等」的证明，绝不作「相等」的证明）。

const _inlineDataFingerprints = new WeakMap<object, string>();

function inlineDataFingerprint(data: unknown): string | undefined {
  if (data === null || typeof data !== "object") return undefined;
  const cached = _inlineDataFingerprints.get(data);
  if (cached !== undefined) return cached;
  let serialized: string;
  try {
    serialized = JSON.stringify(data);
  } catch {
    return undefined; // 循环引用等异常载荷 → 不短路
  }
  let hash = 0x811c9dc5;
  for (let i = 0; i < serialized.length; i++) {
    hash ^= serialized.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  const fp = (hash >>> 0).toString(16);
  _inlineDataFingerprints.set(data, fp);
  return fp;
}

/** 两个源定义是否相等（对 inline GeoJSON 走 content_revision / 指纹短路）。 */
function sourceDefinitionsEqual(prev: unknown, next: unknown): boolean {
  if (prev === next) return true;
  if (
    prev === null || next === null ||
    typeof prev !== "object" || typeof next !== "object"
  ) {
    return isDeepEqual(prev, next);
  }
  const p = prev as Record<string, unknown>;
  const n = next as Record<string, unknown>;

  // 短路 1：同数据版本 —— inlineData 不比较，其余键深比较。
  const pRev = p.content_revision;
  const nRev = n.content_revision;
  const revisionUsable =
    pRev !== undefined && pRev !== null && nRev !== undefined && nRev !== null;
  if (revisionUsable && pRev === nRev) {
    for (const key of new Set([...Object.keys(p), ...Object.keys(n)])) {
      if (key === "inlineData") continue;
      if (!isDeepEqual(p[key], n[key])) return false;
    }
    return true;
  }

  // 短路 2：指纹快败（只证明「不等」）。
  const pFp = inlineDataFingerprint(p.inlineData);
  const nFp = inlineDataFingerprint(n.inlineData);
  if (pFp !== undefined && nFp !== undefined && pFp !== nFp) return false;

  return isDeepEqual(prev, next);
}

function diffView(prev: MapSpecView | undefined, next: MapSpecView | undefined): ViewChange | undefined {
  if (isDeepEqual(prev, next)) return undefined;
  return { prev, next };
}

/**
 * Runtime V4 filter fast path (superseded shape — AC-06 P3): the layer diff
 * is now decomposed once via {@link diffLayerKeys}; a layer whose ONLY change
 * is `filter` (paint/layout keys empty, nothing structural) is reported as
 * the `filter` kind, exactly like the retired isFilterOnlyChange helper but
 * without the rest-spread allocations and without walking the layer twice.
 */

/**
 * AC-06 P3 key-level layer decomposition. Classifies a deep-unequal layer
 * pair into (structural | paintKeys | layoutKeys) WITHOUT allocating the
 * rest-spread copies that isFilterOnlyChange needs:
 *  - any changed key outside paint/layout/filter is structural (type, source,
 *    sourceLayer, cluster, label, visible, …) → recompile;
 *  - paint/layout are flat canonical dicts → per-key deep compare.
 * Exported for the diff/perf test surface.
 */
export interface LayerKeyDiff {
  structural: boolean;
  paintKeys: string[];
  layoutKeys: string[];
}

export function diffLayerKeys(prev: MapSpecLayer, next: MapSpecLayer): LayerKeyDiff {
  const out: LayerKeyDiff = { structural: false, paintKeys: [], layoutKeys: [] };
  const keys = new Set([...Object.keys(prev), ...Object.keys(next)]);
  for (const key of keys) {
    if (key === "paint" || key === "layout" || key === "filter") continue;
    if (
      !isDeepEqual(
        (prev as unknown as Record<string, unknown>)[key],
        (next as unknown as Record<string, unknown>)[key],
      )
    ) {
      out.structural = true;
      return out; // structural dominates — no need to enumerate paint keys
    }
  }
  out.paintKeys = diffFlatDict(
    (prev as unknown as Record<string, unknown>).paint,
    (next as unknown as Record<string, unknown>).paint,
  );
  out.layoutKeys = diffFlatDict(
    (prev as unknown as Record<string, unknown>).layout,
    (next as unknown as Record<string, unknown>).layout,
  );
  return out;
}

function diffFlatDict(a: unknown, b: unknown): string[] {
  const da = a !== null && typeof a === "object" ? (a as Record<string, unknown>) : {};
  const db = b !== null && typeof b === "object" ? (b as Record<string, unknown>) : {};
  const changed: string[] = [];
  for (const key of new Set([...Object.keys(da), ...Object.keys(db)])) {
    if (!isDeepEqual(da[key], db[key])) changed.push(key);
  }
  return changed;
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
    } else if (!sourceDefinitionsEqual(prevSources[id], nextSources[id])) {
      // AC-06 P5: single-entry comparison with content_revision / fingerprint
      // short-circuits inside — a rebuilt-but-equal inline FeatureCollection
      // no longer walks every coordinate (baseline in ac-06-runtime-recon.md).
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
    } else if (changedSourceIds.has(layer.source)) {
      // MapLibre source definitions are not uniformly mutable. Recompile all
      // dependent layers around a source replacement so a same-id vector,
      // raster, URL, or data-generation update cannot leave stale live data.
      // (Checked BEFORE the filter-only fast path: a source update must win
      // even when the layer dict differs only in `filter`.)
      layers.push({ id: layer.id, kind: "recompile", next: layer });
    } else {
      // AC-06 P3/P5：单次键级走查同时完成「是否变化」判定与 patch 分类 ——
      // paint-only / layout-only 走属性 patch（零 remove/add）；结构性字段
      // （type/source/cluster/label/…）保持 recompile；filter 与属性键同帧
      // 变化时各补一条（运行时按 kind 分循环应用）。
      const keyDiff = diffLayerKeys(prevLayer, layer);
      const filterChanged = !isDeepEqual(prevLayer.filter, layer.filter);
      if (keyDiff.structural) {
        layers.push({ id: layer.id, kind: "recompile", next: layer });
      } else if (keyDiff.paintKeys.length > 0 || keyDiff.layoutKeys.length > 0 || filterChanged) {
        if (keyDiff.paintKeys.length > 0) {
          layers.push({ id: layer.id, kind: "paint", next: layer, paintKeys: keyDiff.paintKeys });
        }
        if (keyDiff.layoutKeys.length > 0) {
          layers.push({ id: layer.id, kind: "layout", next: layer, layoutKeys: keyDiff.layoutKeys });
        }
        if (filterChanged) {
          layers.push({ id: layer.id, kind: "filter", next: layer });
        }
      }
      // else: deep-equal → omitted (no-op)
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
