import { diffSpecs, type SpecPatch } from "@/lib/mapspec-compiler/reconciler";
import { diffSpecsAsync } from "@/lib/mapspec-compiler/worker-bridge";
import type { MapSpec, MapSpecSource, MapSpecLayer } from "@/lib/mapspec-compiler/types";
import { RenderDebouncer, type RenderOperation } from "@/lib/map-kit/render-debouncer";
import * as renderer from "@/lib/map-kit/renderer";

/**
 * MapSpecRuntime — the deep module that reconciles a declarative MapSpec
 * against a live MapLibre map instance (ADR-0036).
 *
 *   const rt = new MapSpecRuntime(map)
 *   rt.reconcile(spec)        // diff vs last-applied → minimal patch
 *   rt.getAppliedSpec()       // what the map currently reflects
 *   rt.dispose()              // tear down timers + refs
 *
 * Responsibilities:
 *  - Owns the style-loaded retry loop (was 3 refs in map-panel.tsx).
 *  - Applies the SpecPatch: add/remove/update sources & layers.
 *  - Reuses renderer.addGeoJsonSource/addImageSource/addRasterTileSource so the
 *    F28 (image cache-buster) and F31 (geojson ref-cache) optimizations survive.
 *  - Re-syncs z-order via renderer.syncLayerZOrder after every apply.
 *
 * Deliberately does NOT:
 *  - Apply view changes (Q3 — view stays imperative via flyTo/easeTo in callers).
 *  - Manage interactiveLayerIds / click handling (those stay in map-panel.tsx).
 */

const STYLE_RETRY_MS = 100;

export class MapSpecRuntime {
  private map: any;
  private appliedSpec: MapSpec | null = null;
  private pendingTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private debouncer: RenderDebouncer | null;

  constructor(map: any) {
    this.map = map;
    this.debouncer = new RenderDebouncer(map);
  }

  /**
   * Invalidate the last-applied spec state (e.g. when base style changes).
   * Next reconcile will treat all sources/layers as new and re-apply them.
   */
  invalidateStyle(): void {
    this.appliedSpec = null;
  }

  /**
   * Diff `nextSpec` against the last-applied spec and apply the minimal patch.
   * If the map style isn't loaded yet, schedules a retry (owning the loop that
   * was previously 3 React refs in map-panel.tsx).
   */
  reconcile(nextSpec: MapSpec): void {
    if (this.disposed || !this.map) return;

    // Defer until the base style is loaded. This mirrors map-panel.tsx:167-170
    // but the retry state lives here, not in React refs.
    if (!this.map.isStyleLoaded()) {
      if (this.pendingTimer) clearTimeout(this.pendingTimer);
      this.pendingTimer = setTimeout(() => {
        this.pendingTimer = null;
        this.reconcile(nextSpec);
      }, STYLE_RETRY_MS);
      return;
    }

    const patch = diffSpecs(this.appliedSpec, nextSpec);
    this.applyPatchDirect(patch, nextSpec);
  }

  /**
   * Async reconcile — worker-offloaded diff + frame-budgeted application.
   *
   * Same contract as `reconcile` (diff against last-applied → minimal patch),
   * but: the diff runs in a Web Worker when available (falling back to the
   * main thread), and the patch is applied through a RenderDebouncer so
   * MapLibre mutations are coalesced and time-sliced per rAF frame instead of
   * blocking the UI thread (ADR-0036 / issue #227).
   *
   * Fire-and-forget from callers: `void rt.reconcileAsync(spec)`.
   * `flush()` drains pending operations synchronously (tests / screenshots).
   */
  async reconcileAsync(nextSpec: MapSpec): Promise<void> {
    if (this.disposed || !this.map) return;

    // Defer until the base style is loaded (mirrors the sync retry loop).
    if (!this.map.isStyleLoaded()) {
      await new Promise((resolve) => setTimeout(resolve, STYLE_RETRY_MS));
      if (this.disposed || !this.map) return;
      return this.reconcileAsync(nextSpec);
    }

    const patch = await diffSpecsAsync(this.appliedSpec, nextSpec);
    if (this.disposed || !this.map) return;
    this.applyPatchDebounced(patch, nextSpec);
  }

  /**
   * Synchronously drain all pending debounced operations (e.g. before unmount
   * or taking a screenshot). No-op when nothing is queued.
   */
  flush(): void {
    this.debouncer?.flush();
  }

  /**
   * Apply a SpecPatch immediately (synchronous path). Kept as the
   * correctness-preserving reference used by `reconcile()` and the sync
   * test suite; the debounced path enqueues the same operations in the same
   * strict order.
   */
  private applyPatchDirect(patch: SpecPatch, nextSpec: MapSpec): void {
    // --- layers (remove + recompile may free sources) ---
    for (const change of patch.layers) {
      if (change.kind === "remove") {
        this.removeLayerSafe(change.id);
      } else if (change.kind === "recompile") {
        // Q3 fallback: remove + re-add rather than diffing individual paint props.
        this.removeLayerSafe(change.id);
      }
      // `add` is handled below, after sources are ready.
    }

    // --- sources ---
    for (const change of patch.sources) {
      if (change.kind === "remove") {
        this.removeSourceSafe(change.id);
      } else if ((change.kind === "add" || change.kind === "update") && change.next) {
        // add/update both route through the idempotent renderer helpers (they
        // carry the F28/F31 cache logic). Tile-URL sources carry no cache state
        // and addRasterTileSource is itself idempotent.
        this.applySource(change.id, change.next);
      }
    }

    // --- layers (add + recompile re-add) ---
    for (const change of patch.layers) {
      if ((change.kind === "add" || change.kind === "recompile") && change.next) {
        this.addLayerSafe(change.next);
      }
    }

    // --- z-order: re-sync unconditionally (cheap, and handles reordering that
    // the layer diff treats as unchanged). Q3.
    const orderedIds = nextSpec.layers.map((l) => l.id);
    renderer.syncLayerZOrder(this.map, "", orderedIds);

    this.appliedSpec = nextSpec;
  }

  /**
   * Enqueue a SpecPatch as coalesced, frame-budgeted render operations.
   * Ordering mirrors applyPatchDirect exactly (layers-remove → sources →
   * layers-add → z-order), all high priority so the sequence within a frame
   * is preserved — MapLibre requires a layer's source to exist first.
   */
  private applyPatchDebounced(patch: SpecPatch, nextSpec: MapSpec): void {
    const ops: RenderOperation[] = [];

    for (const change of patch.layers) {
      if (change.kind === "remove" || change.kind === "recompile") {
        ops.push({
          id: `layer:remove:${change.id}`,
          type: "REMOVE_LAYER",
          priority: "high",
          execute: () => this.removeLayerSafe(change.id),
        });
      }
    }

    for (const change of patch.sources) {
      if (change.kind === "remove") {
        ops.push({
          id: `source:remove:${change.id}`,
          type: "REMOVE_LAYER",
          priority: "high",
          execute: () => this.removeSourceSafe(change.id),
        });
      } else if ((change.kind === "add" || change.kind === "update") && change.next) {
        const next = change.next;
        ops.push({
          id: `source:apply:${change.id}`,
          type: "UPDATE_GEOJSON",
          priority: "high",
          execute: () => this.applySource(change.id, next),
        });
      }
    }

    for (const change of patch.layers) {
      if ((change.kind === "add" || change.kind === "recompile") && change.next) {
        const next = change.next;
        ops.push({
          id: `layer:add:${change.id}`,
          type: "ADD_LAYER",
          priority: "high",
          execute: () => this.addLayerSafe(next),
        });
      }
    }

    const orderedIds = nextSpec.layers.map((l) => l.id);
    ops.push({
      id: "zorder:sync",
      type: "SET_STYLE",
      priority: "high",
      execute: () => renderer.syncLayerZOrder(this.map, "", orderedIds),
    });

    for (const op of ops) {
      this.debouncer?.enqueue(op);
    }
    this.appliedSpec = nextSpec;
  }

  getAppliedSpec(): MapSpec | null {
    return this.appliedSpec;
  }

  dispose(): void {
    this.disposed = true;
    if (this.pendingTimer) {
      clearTimeout(this.pendingTimer);
      this.pendingTimer = null;
    }
    this.debouncer?.dispose();
    this.debouncer = null;
    this.map = null;
    this.appliedSpec = null;
  }

  // ---- source/layer application helpers ----

  /**
   * Translate a MapSpecSource into the MapLibre call. Reuses the existing
   * renderer helpers so F28 (image cache-buster) and F31 (geojson ref-cache)
   * optimizations are preserved exactly.
   */
  private applySource(id: string, source: MapSpecSource): void {
    if (source.type === "raster") {
      // Raster image source (HeatmapRasterSource path).
      renderer.addImageSource(
        this.map,
        id,
        source.imageRef,
        boundsToImageCorners(source.bounds),
      );
    } else if (source.inlineData) {
      renderer.addGeoJsonSource(this.map, id, source.inlineData);
    } else {
      const tileUrl = source.url || source.dataPath;
      if (tileUrl) {
        // Tile URL source (raster/tile layer). The adapter emits this shape for
        // string-sourced layers. addRasterTileSource is idempotent (no-op if exists).
        renderer.addRasterTileSource(this.map, id, tileUrl);
      }
    }
    // else: nothing to apply (empty fallback source already added? skip).
  }

  private addLayerSafe(layer: MapSpecLayer): void {
    // MapSpecLayer is already shaped close to a MapLibre layer spec. We pass it
    // through with only the fields MapLibre expects.
    const def: any = {
      id: layer.id,
      type: layer.type,
      source: layer.source,
      paint: layer.paint || {},
      layout: layer.layout || {},
    };
    if (layer.filter) def.filter = layer.filter;
    // Some layer types carry maxzoom (native heatmap). MapSpecLayer doesn't
    // model that today; if needed, extend the type. For now omit.
    try {
      this.map.addLayer(def);
    } catch (err) {
      // Defensive: a recompile that races with a style swap may find the layer
      // already re-added by the styledata path. Log and continue rather than
      // throwing the whole reconcile.
      // eslint-disable-next-line no-console
      console.warn(`[MapSpecRuntime] addLayer failed for ${layer.id}:`, err);
    }
  }

  private removeLayerSafe(id: string): void {
    if (this.map.getLayer(id)) {
      try { this.map.removeLayer(id); } catch { /* already gone */ }
    }
  }

  private removeSourceSafe(id: string): void {
    // Must remove all layers referencing this source first. diffSpecs already
    // reported dependent layer removes, but defensive: scrub any stragglers.
    const style = this.map.getStyle();
    if (style?.layers) {
      for (const l of style.layers) {
        if (l.source === id && this.map.getLayer(l.id)) {
          try { this.map.removeLayer(l.id); } catch { /* silent */ }
        }
      }
    }
    if (this.map.getSource(id)) {
      try { this.map.removeSource(id); } catch { /* silent */ }
    }
  }
}

/** WGS84 bounds [w, s, e, n] → MapLibre image corners [TL, TR, BR, BL]. */
function boundsToImageCorners(bounds: [number, number, number, number]): [[number, number], [number, number], [number, number], [number, number]] {
  const [w, s, e, n] = bounds;
  return [
    [w, n],
    [e, n],
    [e, s],
    [w, s],
  ];
}
