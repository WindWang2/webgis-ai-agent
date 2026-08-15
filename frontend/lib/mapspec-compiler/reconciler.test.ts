import { describe, it, expect } from "vitest";
import { diffSpecs } from "./reconciler";
import { MapSpec } from "./types";

// ADR-0036: pure diffSpecs — given two MapSpecs (or null → spec), reports the
// minimal source/layer/view patch. Pure (no MapLibre), unit-tested here.

function baseSpec(overrides: Partial<MapSpec> = {}): MapSpec {
  return {
    version: "1.0",
    sources: {
      pts: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
    },
    layers: [
      { id: "pts-circle", source: "pts", type: "circle", paint: { color: "#ff0000", radius: 6 } },
    ],
    ...overrides,
  };
}

describe("MapSpec Reconciler (ADR-0036)", () => {
  describe("diffSpecs — null → spec (first application)", () => {
    it("reports every source and layer as add", () => {
      const patch = diffSpecs(null, baseSpec());
      expect(patch.sources).toEqual([
        { id: "pts", kind: "add", next: expect.any(Object) },
      ]);
      expect(patch.layers).toEqual([
        { id: "pts-circle", kind: "add", next: expect.any(Object) },
      ]);
    });

    it("includes view change when next defines one", () => {
      const spec = baseSpec({ view: { center: [10, 20], zoom: 5 } });
      const patch = diffSpecs(null, spec);
      expect(patch.view).toEqual({ prev: undefined, next: { center: [10, 20], zoom: 5 } });
    });

    it("omits view when next has none", () => {
      const patch = diffSpecs(null, baseSpec());
      expect(patch.view).toBeUndefined();
    });

    it("handles an empty spec", () => {
      const patch = diffSpecs(null, { version: "1.0", sources: {}, layers: [] });
      expect(patch.sources).toEqual([]);
      expect(patch.layers).toEqual([]);
    });
  });

  describe("diffSpecs — identical specs", () => {
    it("yields an empty patch (the runtime no-op fast path)", () => {
      const spec = baseSpec();
      const patch = diffSpecs(spec, spec);
      expect(patch.sources).toEqual([]);
      expect(patch.layers).toEqual([]);
      expect(patch.view).toBeUndefined();
    });

    it("yields an empty patch for structurally-equal but distinct-object specs", () => {
      const a = baseSpec();
      const b = baseSpec();
      const patch = diffSpecs(a, b);
      expect(patch.sources).toEqual([]);
      expect(patch.layers).toEqual([]);
    });
  });

  describe("diffSpecs — source changes", () => {
    it("reports a new source as add", () => {
      const next = baseSpec({
        sources: {
          pts: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
          lines: { type: "geojson", url: "/lines.json" },
        },
      });
      const patch = diffSpecs(baseSpec(), next);
      expect(patch.sources).toContainEqual({ id: "lines", kind: "add", next: next.sources.lines });
      expect(patch.sources.find((c) => c.id === "pts")).toBeUndefined();
    });

    it("reports a removed source as remove", () => {
      const prev = baseSpec({
        sources: {
          pts: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
          gone: { type: "geojson", url: "/gone.json" },
        },
      });
      const patch = diffSpecs(prev, baseSpec());
      expect(patch.sources).toEqual([{ id: "gone", kind: "remove" }]);
    });

    it("reports a changed geojson inlineData ref as update", () => {
      const prev = baseSpec();
      const next = baseSpec({
        sources: {
          pts: { type: "geojson", inlineData: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [0, 0] } }] } },
        },
      });
      const patch = diffSpecs(prev, next);
      expect(patch.sources).toEqual([{ id: "pts", kind: "update", next: next.sources.pts }]);
    });

    it("reports a changed raster imageRef/bounds as update", () => {
      const prev: MapSpec = {
        version: "1.0",
        sources: { heat: { type: "raster", imageRef: "ref:raster/a", bounds: [0, 0, 1, 1] } },
        layers: [],
      };
      const next: MapSpec = {
        version: "1.0",
        sources: { heat: { type: "raster", imageRef: "ref:raster/b", bounds: [0, 0, 1, 1] } },
        layers: [],
      };
      const patch = diffSpecs(prev, next);
      expect(patch.sources).toEqual([{ id: "heat", kind: "update", next: next.sources.heat }]);
    });
  });

  describe("diffSpecs — layer changes", () => {
    it("reports a new layer as add", () => {
      const next = baseSpec({
        layers: [
          { id: "pts-circle", source: "pts", type: "circle", paint: { color: "#ff0000", radius: 6 } },
          { id: "pts-label", source: "pts", type: "symbol", layout: { labelField: "name" } },
        ],
      });
      const patch = diffSpecs(baseSpec(), next);
      expect(patch.layers).toContainEqual({ id: "pts-label", kind: "add", next: next.layers[1] });
    });

    it("reports a removed layer as remove", () => {
      const prev = baseSpec({
        layers: [
          { id: "pts-circle", source: "pts", type: "circle", paint: { color: "#ff0000", radius: 6 } },
          { id: "pts-label", source: "pts", type: "symbol", layout: {} },
        ],
      });
      const patch = diffSpecs(prev, baseSpec());
      expect(patch.layers).toEqual([{ id: "pts-label", kind: "remove" }]);
    });

    it("reports a changed paint property as recompile", () => {
      const next = baseSpec({
        layers: [
          { id: "pts-circle", source: "pts", type: "circle", paint: { color: "#00ff00", radius: 6 } },
        ],
      });
      const patch = diffSpecs(baseSpec(), next);
      expect(patch.layers).toEqual([{ id: "pts-circle", kind: "recompile", next: next.layers[0] }]);
    });

    it("reports a changed layer type as recompile", () => {
      const next = baseSpec({
        layers: [
          { id: "pts-circle", source: "pts", type: "line", paint: { color: "#ff0000" } },
        ],
      });
      const patch = diffSpecs(baseSpec(), next);
      expect(patch.layers[0].kind).toBe("recompile");
    });
  });

  describe("diffSpecs — layer ordering", () => {
    it("reports reordered layers as recompile (order is encoded in patch order)", () => {
      const a = baseSpec({
        layers: [
          { id: "l1", source: "pts", type: "circle" },
          { id: "l2", source: "pts", type: "circle" },
        ],
      });
      const b = baseSpec({
        layers: [
          { id: "l2", source: "pts", type: "circle" },
          { id: "l1", source: "pts", type: "circle" },
        ],
      });
      // Layers are structurally equal but reordered. Signature compares the
      // layer object itself (unchanged), so neither is recompiled — the diff
      // deliberately stays silent about positions (FIX-3-9/#375). The runtime
      // detects the order change with its own cheap order-key tracker and
      // re-syncs z-order, so reordering is handled there, not via recompile
      // noise.
      const patch = diffSpecs(a, b);
      expect(patch.layers).toEqual([]);
    });
  });

  describe("diffSpecs — view", () => {
    it("reports a zoom change", () => {
      const prev = baseSpec({ view: { zoom: 5 } });
      const next = baseSpec({ view: { zoom: 7 } });
      const patch = diffSpecs(prev, next);
      expect(patch.view).toEqual({ prev: { zoom: 5 }, next: { zoom: 7 } });
    });

    it("reports a center change", () => {
      const prev = baseSpec({ view: { center: [10, 20] } });
      const next = baseSpec({ view: { center: [11, 21] } });
      const patch = diffSpecs(prev, next);
      expect(patch.view?.next?.center).toEqual([11, 21]);
    });

    it("omits view when unchanged", () => {
      const prev = baseSpec({ view: { zoom: 5, center: [10, 20] } });
      const next = baseSpec({ view: { zoom: 5, center: [10, 20] } });
      const patch = diffSpecs(prev, next);
      expect(patch.view).toBeUndefined();
    });
  });
});
