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
      sources[id] = { ...def, setData: vi.fn() };
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

      expect(map._calls.addSource.map((c: any) => c.id)).toEqual(["L1"]);
      expect(map._calls.addLayer.map((c: any) => c.def.id)).toEqual(["L1__point"]);
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
      expect(map._calls.addLayer.map((c: any) => c.def.id)).toEqual(["L1__point"]);
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

      expect(map._calls.addSource.map((c: any) => c.id)).toEqual(["L1"]);
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

  describe("invalidateStyle", () => {
    it("resets appliedSpec to null so next reconcile re-applies all sources and layers", () => {
      const rt = new MapSpecRuntime(map);
      rt.reconcile(pointSpec());
      expect(rt.getAppliedSpec()).not.toBeNull();
      map._calls.addSource.length = 0;
      map._calls.addLayer.length = 0;

      // Simulate MapLibre clearing existing sources/layers when base style changes
      delete map._sources["L1"];

      rt.invalidateStyle();
      expect(rt.getAppliedSpec()).toBeNull();

      rt.reconcile(pointSpec());
      expect(map._calls.addSource.map((c: any) => c.id)).toEqual(["L1"]);
      expect(map._calls.addLayer.map((c: any) => c.def.id)).toEqual(["L1__point"]);
    });
  });

  describe("reconcileAsync — worker fallback + debounced apply", () => {
    beforeEach(() => {
      // No Worker in the test env → the bridge falls back to a sync diff.
      vi.stubGlobal("Worker", undefined);
    });
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("applies the spec once flushed (fallback diff path)", async () => {
      const rt = new MapSpecRuntime(map);
      const pending = rt.reconcileAsync(pointSpec());
      // appliedSpec must NOT lead the map: it is only updated once the ops
      // have actually executed (z-order op). Before flush it stays null.
      expect(rt.getAppliedSpec()).toBeNull();
      rt.flush();
      await pending;
      expect(rt.getAppliedSpec()).toEqual(pointSpec());
      expect(map._calls.addSource.map((c: any) => c.id)).toEqual(["L1"]);
      expect(map._calls.addLayer.map((c: any) => c.def.id)).toEqual(["L1__point"]);
    });

    it("is a no-op for an identical spec (nothing applied after flush)", async () => {
      const rt = new MapSpecRuntime(map);
      let p = rt.reconcileAsync(pointSpec());
      rt.flush();
      await p;
      map._calls.addSource.length = 0;
      map._calls.addLayer.length = 0;

      p = rt.reconcileAsync(pointSpec());
      rt.flush();
      await p;

      expect(map._calls.addSource).toEqual([]);
      expect(map._calls.addLayer).toEqual([]);
      expect(map._calls.removeLayer).toEqual([]);
    });

    it("defers until the style is loaded, then applies once", async () => {
      vi.useFakeTimers();
      let loaded = false;
      map.isStyleLoaded = () => loaded;
      const rt = new MapSpecRuntime(map);

      const pending = rt.reconcileAsync(pointSpec());
      expect(map._calls.addSource).toEqual([]);

      loaded = true;
      await vi.advanceTimersByTimeAsync(150);
      rt.flush(); // drive the debouncer so the z-order completion op runs
      await pending;

      expect(map._calls.addSource.map((c: any) => c.id)).toEqual(["L1"]);
      expect(map._calls.addLayer.map((c: any) => c.def.id)).toEqual(["L1__point"]);
      vi.useRealTimers();
    });

    // ── appliedSpec timing correctness (rapid-edit regression) ─────────────

    it("rapid consecutive reconcileAsync must not drop layer updates", async () => {
      // Bug: appliedSpec used to be updated at ENQUEUE time while ops execute
      // next frame. specA then specB (before the debouncer drains) made the
      // second diff compute against specA (which the map never reflected) and
      // the debouncer coalesced `source:apply:L1` (B replaced A) — the first
      // patch's layer add carried specA's paint while appliedSpec already said
      // specB. The paint update was permanently lost.
      const specA: MapSpec = {
        version: "1.0",
        sources: { L1: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } } },
        layers: [{ id: "L1__point", source: "L1", type: "circle", paint: { "circle-radius": 6 } }],
      };
      const specB: MapSpec = {
        ...specA,
        layers: [{ id: "L1__point", source: "L1", type: "circle", paint: { "circle-radius": 99 } }],
      };

      const rt = new MapSpecRuntime(map);
      const p1 = rt.reconcileAsync(specA);
      const p2 = rt.reconcileAsync(specB);
      // Drive the debouncer until both requests have fully applied.
      rt.flush();
      await p1;
      rt.flush();
      await p2;

      // The MAP's final state (not the call log — recompiles remove+re-add)
      // must reflect specB.
      const applied = map.getLayer("L1__point");
      expect(applied.paint["circle-radius"]).toBe(99); // specB wins
      expect(rt.getAppliedSpec()).toEqual(specB);
    });

    it("rapid add-then-remove of a layer ends with the layer removed", async () => {
      const withLayer: MapSpec = {
        version: "1.0",
        sources: { L1: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } } },
        layers: [{ id: "L1__point", source: "L1", type: "circle", paint: { "circle-radius": 6 } }],
      };
      const withoutLayer: MapSpec = {
        version: "1.0",
        sources: { L1: withLayer.sources.L1 },
        layers: [],
      };

      const rt = new MapSpecRuntime(map);
      const p1 = rt.reconcileAsync(withLayer);
      const p2 = rt.reconcileAsync(withoutLayer);
      rt.flush();
      await p1;
      rt.flush();
      await p2;

      expect(map.getLayer("L1__point")).toBeUndefined();
      expect(rt.getAppliedSpec()).toEqual(withoutLayer);
    });

    it("getAppliedSpec stays null until ops drain (first apply)", async () => {
      const rt = new MapSpecRuntime(map);
      const pending = rt.reconcileAsync(pointSpec());
      // Even though diff+enqueue happened, the map does not reflect the spec yet.
      expect(rt.getAppliedSpec()).toBeNull();
      rt.flush();
      await pending;
      expect(rt.getAppliedSpec()).toEqual(pointSpec());
    });

    it("dispose during a pending apply releases the in-flight promise", async () => {
      const rt = new MapSpecRuntime(map);
      const pending = rt.reconcileAsync(pointSpec());
      rt.dispose(); // queue dropped → the z-order completion op never runs
      await pending; // must resolve, not hang
      expect(rt.getAppliedSpec()).toBeNull();
    });

    it("continues promise chain even if processOne throws an error", async () => {
      const rt = new MapSpecRuntime(map);
      vi.spyOn(rt as any, "processOne").mockRejectedValueOnce(new Error("processOne error"));
      await rt.reconcileAsync(pointSpec("L1"));

      const p2 = rt.reconcileAsync(pointSpec("L2"));
      rt.flush();
      await p2;
      expect(rt.getAppliedSpec()).toEqual(pointSpec("L2"));
    });
  });
});

describe("MapSpecRuntime — Data Plane vector tile source", () => {
  let map: any;
  beforeEach(() => { map = makeMockMap(); });

  function vectorSpec(): MapSpec {
    return {
      version: "1.0",
      sources: {
        V1: {
          type: "vector",
          tiles: ["http://x/tiles/{z}/{x}/{y}.mvt?session_id=s"],
          minzoom: 1,
          maxzoom: 16,
        } as any,
      },
      layers: [
        { id: "V1__point", source: "V1", type: "circle", paint: { "circle-radius": 6 } },
      ],
    };
  }

  it("adds a vector source and tags sublayers with source-layer data", () => {
    const rt = new MapSpecRuntime(map);
    rt.reconcile(vectorSpec());

    const srcCall = map._calls.addSource.find((c: any) => c.id === "V1");
    expect(srcCall.def.type).toBe("vector");
    expect(srcCall.def.tiles[0]).toContain("{z}/{x}/{y}");
    // MapLibre requires source-layer for vector sources
    const layerCall = map._calls.addLayer.find((c: any) => c.def.id === "V1__point");
    expect(layerCall.def["source-layer"]).toBe("data");
  });

  it("replaces a stale geojson source when the same id upgrades to vector", () => {
    const rt = new MapSpecRuntime(map);
    rt.reconcile(pointSpec("V1")); // geojson source first (empty pre-fetch FC)
    map._calls.addSource.length = 0;

    rt.reconcile(vectorSpec()); // big FC arrived → upgrade to vector tiles

    // addVectorTileSource removed the old geojson source then added vector
    const srcCalls = map._calls.addSource.filter((c: any) => c.id === "V1");
    expect(srcCalls.length).toBe(1);
    expect(srcCalls[0].def.type).toBe("vector");
  });
});
