import { diffSpecs } from "@/lib/mapspec-compiler/reconciler";
import type { MapSpec, MapSpecSource, MapSpecLayer } from "@/lib/mapspec-compiler/types";
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

  constructor(map: any) {
    this.map = map;
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

    // --- sources ---
    // Removes must come before layer removes that reference them; the patch
    // already orders sources arbitrarily, so we remove dependent layers first
    // (below) — but MapLibre requires a source's layers gone before removeSource.
    // We handle that by removing layers first (patch.layers), then sources.
    // To be safe, we also remove layers whose source is being removed.
    const removedSourceIds = new Set(patch.sources.filter((s) => s.kind === "remove").map((s) => s.id));

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
    void removedSourceIds; // (kept for clarity; removes already handled above)
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
