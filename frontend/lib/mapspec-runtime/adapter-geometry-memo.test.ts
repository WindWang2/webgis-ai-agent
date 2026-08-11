import { describe, it, expect, beforeEach } from "vitest";
import {
  hudStateToMapSpec,
  _resetGeometryProfileCacheForTests,
  _geometryProfileStats,
} from "./adapter";
import type { Layer } from "@/lib/types/layer";

// FE-3 (design §7): the geometry-mix scans in hudStateToMapSpec are memoized
// via a WeakMap keyed on the GeoJSON FeatureCollection reference (findings E2).
// These tests pin that the memo preserves identical output AND that the same
// data reference is never rescanned.

function fcWith(...geometryTypes: string[]): any {
  return {
    type: "FeatureCollection",
    features: geometryTypes.map((t) => ({
      type: "Feature",
      properties: {},
      geometry: { type: t, coordinates: t === "Point" ? [0, 0] : [[0, 0], [1, 1]] },
    })),
  };
}

function baseLayer(source: any, overrides: Partial<Layer> = {}): Layer {
  return {
    id: "L1",
    name: "Layer 1",
    type: "vector",
    visible: true,
    opacity: 1,
    source,
    style: { color: "#16a34a", strokeColor: "#16a34a" },
    ...overrides,
  };
}

function toSpec(layers: Layer[]) {
  return hudStateToMapSpec({ layers, processLayers: {}, activeFilters: {}, is3D: false });
}

describe("hudStateToMapSpec geometry-mix memo (FE-3)", () => {
  beforeEach(() => {
    _resetGeometryProfileCacheForTests();
  });

  it("does not rescan the same data reference on repeated reconciles", () => {
    const src = fcWith("Point");
    const layer = baseLayer(src);

    toSpec([layer]);
    expect(_geometryProfileStats.scanCount).toBe(1);

    // Same reference, second reconcile → cache hit, no new scan.
    toSpec([layer]);
    toSpec([layer]);
    expect(_geometryProfileStats.scanCount).toBe(1);
  });

  it("produces byte-identical specs across cache hits", () => {
    const src = fcWith("Point");
    const layer = baseLayer(src);

    const first = toSpec([layer]);
    const second = toSpec([layer]);
    expect(second).toEqual(first);
    expect(_geometryProfileStats.scanCount).toBe(1);
  });

  it("rescans exactly once per new data reference (correctness preserved)", () => {
    const layerA = baseLayer(fcWith("Polygon"));
    const layerB = baseLayer(fcWith("Point", "LineString"));

    toSpec([layerA]);
    toSpec([layerA]); // cache hit
    toSpec([layerB]); // new reference → one new scan
    toSpec([layerB]); // cache hit

    expect(_geometryProfileStats.scanCount).toBe(2);
  });

  it("keeps the weight-interpolation radius behavior for a cached profile", () => {
    // Point features with a weight property → interpolated circle-radius.
    // The 2nd (cached) pass must produce the same paint as the 1st.
    const src = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { weight: 0.5 },
          geometry: { type: "Point", coordinates: [0, 0] },
        },
      ],
    };
    const layer = baseLayer(src);

    const first = toSpec([layer]);
    const second = toSpec([layer]);

    const firstRadius = first.layers.find((l) => l.id === "L1__point")!.paint!["circle-radius"];
    const secondRadius = second.layers.find((l) => l.id === "L1__point")!.paint!["circle-radius"];
    expect(secondRadius).toEqual(firstRadius);
    expect(secondRadius).toEqual(["interpolate", ["linear"], ["get", "weight"], 0, 4, 1, 8]);
    expect(_geometryProfileStats.scanCount).toBe(1);
  });

  it("handles non-GeoJSON sources without scanning (empty profile)", () => {
    const tileLayer = baseLayer("https://tiles.example/{z}/{x}/{y}.png", { type: "tile" });
    const out = toSpec([tileLayer]);
    expect(out.layers.length).toBe(1); // raster tile sublayer emitted
    expect(_geometryProfileStats.scanCount).toBe(0);
  });
});
