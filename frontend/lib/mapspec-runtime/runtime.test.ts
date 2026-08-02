import { describe, it, expect, vi, beforeEach } from "vitest";
import { MapSpecRuntime } from "./runtime";
import type { MapSpec } from "@/lib/mapspec-compiler/types";

// ADR-0036: MapSpecRuntime — applies a SpecPatch to a live (here: mocked) map.
// House style: do NOT vi.mock('maplibre-gl'); hand-roll a stub map that records
// the calls the runtime makes, then assert on the call sequence.

function makeMockMap() {
  // Internal mutable style state so getSource/getLayer/getStyle behave like a
  // real map's bookkeeping. The runtime reads these to decide add-vs-update.
  const sources: Record<string, any> = {};
  const layers: any[] = [];

  const calls = {
    addSource: [] as Array<{ id: string; def: any }>,
    removeSource: [] as string[],
    addLayer: [] as Array<{ def: any }>,
    removeLayer: [] as string[],
    moveLayer: [] as string[],
    setData: [] as Array<{ id: string; data: any }>,
  };

  const map: any = {
    isStyleLoaded: () => true,
    getStyle: () => ({ sources, layers }),
    getSource(id: string) { return sources[id]; },
    getLayer(id: string) { return layers.find((l) => l.id === id); },
    addSource(id: string, def: any) {
      sources[id] = def;
      calls.addSource.push({ id, def });
    },
    removeSource(id: string) {
      delete sources[id];
      calls.removeSource.push(id);
    },
    addLayer(def: any) {
      layers.push(def);
      calls.addLayer.push({ def });
    },
    removeLayer(id: string) {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    },
    moveLayer(id: string) {
      calls.moveLayer.push(id);
    },
    // GeoJSON source stub used by renderer.addGeoJsonSource
    _sources: sources,
    _calls: calls,
  };
  return map;
}

function pointSpec(layerId = "L1"): MapSpec {
  return {
    version: "1.0",
    sources: {
      [layerId]: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
    },
    layers: [
      { id: `${layerId}__point`, source: layerId, type: "circle", paint: { "circle-radius": 6 } },
    ],
  };
}

describe("MapSpecRuntime (ADR-0036)", () => {
  let map: any;
  beforeEach(() => { map = makeMockMap(); });

  describe("reconcile — first application", () => {
    it("adds every source and layer from the spec", () => {
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());

      expect(map._calls.addSource.map((c) => c.id)).toEqual(["L1"]);
      expect(map._calls.addLayer.map((c) => c.def.id)).toEqual(["L1__point"]);
      expect(rt.getAppliedSpec()).toEqual(pointSpec());
    });

    it("does not touch the view (Q3: view stays imperative)", () => {
      const rt = new MapSpecRuntime(map);
      const spec = pointSpec();
      (spec as any).view = { center: [10, 20], zoom: 5 };
      // Spy on view methods to assert they are never called.
      map.setCenter = vi.fn();
      map.setZoom = vi.fn();
      map.flyTo = vi.fn();
      map.easeTo = vi.fn();
      rt.reconcile(spec);
      expect(map.setCenter).not.toHaveBeenCalled();
      expect(map.setZoom).not.toHaveBeenCalled();
      expect(map.flyTo).not.toHaveBeenCalled();
      expect(map.easeTo).not.toHaveBeenCalled();
    });
  });

  describe("reconcile — identical spec (no-op fast path)", () => {
    it("makes no add/remove calls on the second reconcile of the same spec", () => {
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());
      map._calls.addSource.length = 0;
      map._calls.addLayer.length = 0;
      map._calls.removeLayer.length = 0;

      rt.reconcile(pointSpec());

      expect(map._calls.addSource).toEqual([]);
      expect(map._calls.addLayer).toEqual([]);
      expect(map._calls.removeLayer).toEqual([]);
    });
  });

  describe("reconcile — removing a layer", () => {
    it("removes only the absent layer, leaves others untouched", () => {
      const rt = new MapSpecRuntime(map);
      const twoLayers: MapSpec = {
        version: "1.0",
        sources: {
          A: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
          B: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
        },
        layers: [
          { id: "A__point", source: "A", type: "circle", paint: { "circle-radius": 6 } },
          { id: "B__point", source: "B", type: "circle", paint: { "circle-radius": 6 } },
        ],
      };
      rt.reconcile(twoLayers);
      map._calls.removeLayer.length = 0;
      map._calls.removeSource.length = 0;

      const minusB: MapSpec = {
        version: "1.0",
        sources: { A: twoLayers.sources.A },
        layers: [twoLayers.layers[0]],
      };
      rt.reconcile(minusB);

      expect(map._calls.removeLayer).toEqual(["B__point"]);
      expect(map._calls.removeSource).toEqual(["B"]);
      // A untouched.
      expect(map.getSource("A")).toBeDefined();
      expect(map.getLayer("A__point")).toBeDefined();
    });
  });

  describe("reconcile — changed paint triggers recompile", () => {
    it("removes then re-adds only the changed layer", () => {
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());
      map._calls.addLayer.length = 0;
      map._calls.removeLayer.length = 0;

      const changed: MapSpec = {
        version: "1.0",
        sources: { L1: pointSpec().sources.L1 },
        layers: [
          { id: "L1__point", source: "L1", type: "circle", paint: { "circle-radius": 99 } },
        ],
      };
      rt.reconcile(changed);

      expect(map._calls.removeLayer).toEqual(["L1__point"]);
      expect(map._calls.addLayer.map((c) => c.def.id)).toEqual(["L1__point"]);
      expect(map._calls.addLayer[0].def.paint["circle-radius"]).toBe(99);
    });
  });

  describe("reconcile — style-not-loaded retry", () => {
    it("defers application until isStyleLoaded becomes true, then applies once", async () => {
      vi.useFakeTimers();
      let loaded = false;
      map.isStyleLoaded = () => loaded;
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());

      // Not applied yet.
      expect(map._calls.addSource).toEqual([]);

      loaded = true;
      await vi.advanceTimersByTimeAsync(150);

      expect(map._calls.addSource.map((c) => c.id)).toEqual(["L1"]);
      vi.useRealTimers();
    });
  });

  describe("z-order sync", () => {
    it("calls syncLayerZOrder with the next spec's layer order", () => {
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());
      // moveLayer is invoked by syncLayerZOrder; presence confirms the path ran.
      // (Exact count depends on helper internals; we assert it was called.)
      expect(map._calls.moveLayer.length).toBeGreaterThan(0);
    });
  });

  describe("dispose", () => {
    it("drops the map ref so subsequent reconcile is a no-op", () => {
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());
      rt.dispose();

      const before = map._calls.addLayer.length;
      rt.reconcile(pointSpec());
      expect(map._calls.addLayer.length).toBe(before);
    });

    it("cancels a pending style-not-loaded retry", async () => {
      vi.useFakeTimers();
      let loaded = false;
      map.isStyleLoaded = () => loaded;
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());
      rt.dispose();
      loaded = true;
      await vi.advanceTimersByTimeAsync(500);
      expect(map._calls.addSource).toEqual([]);
      vi.useRealTimers();
    });
  });

  describe("getAppliedSpec", () => {
    it("returns null before the first successful reconcile", () => {
      const rt = new MapSpecRuntime(map);
      expect(rt.getAppliedSpec()).toBeNull();
    });
  });
});
