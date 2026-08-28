import { describe, it, expect } from "vitest";
import { hudStateToMapSpec } from "@/lib/mapspec-runtime/adapter";
import type { Layer } from "@/lib/types/layer";
import type { GeoJSONFeatureCollection } from "@/lib/types";

/**
 * ADR-0078 adapter drift-fix regression. Before the fix, a thematic layer
 * (create_thematic_map result) rendered every feature as a flat `style.color`
 * / `#16a34a` while <ThematicLegend> showed a full graduated palette — because
 * the adapter painted from a pre-baked `fill_color` property that the backend
 * never set. Now the adapter derives the MapLibre color expression from the
 * SAME legend_spec the legend reads, so map and legend agree.
 */

function polyFeatures(n = 3): GeoJSONFeatureCollection {
  return {
    type: "FeatureCollection",
    features: Array.from({ length: n }, (_, i) => ({
      type: "Feature" as const,
      geometry: { type: "Polygon" as const, coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
      properties: { pop: i * 10 },
    })),
  };
}

function layer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: "thematic-1", name: "t", type: "vector", visible: true, opacity: 1,
    source: polyFeatures(), ...overrides,
  } as Layer;
}

describe("adapter derives thematic paint from legend_spec (drift fix)", () => {
  it("emits a step fill-color from a graduated legend_spec, NOT flat #16a34a", () => {
    const l = layer({
      legend_spec: {
        type: "graduated", field: "pop", breaks: [0, 10, 20, 30], palette: "YlOrRd",
        palette_colors: ["#ffffb2", "#fd8d3c", "#bd0026"],
      } as any,
      style: { color: "#16a34a" },
    });
    const spec = hudStateToMapSpec({ layers: [l], processLayers: {}, activeFilters: {}, is3D: false });
    const fill = spec.layers.find((x) => x.id.endsWith("__fill"))!;
    expect(fill.paint!["fill-color"]).toEqual([
      "step", ["to-number", ["get", "pop"]], "#ffffb2",
      10, "#fd8d3c", 20, "#bd0026",
    ]);
  });

  it("the legend field is the SAME field the paint reads (no field drift)", () => {
    const l = layer({
      legend_spec: {
        type: "graduated", field: "income", breaks: [0, 10, 20], palette: "YlOrRd",
        palette_colors: ["#a", "#b"],
      } as any,
    });
    const spec = hudStateToMapSpec({ layers: [l], processLayers: {}, activeFilters: {}, is3D: false });
    const fill = spec.layers.find((x) => x.id.endsWith("__fill"))!;
    // paint references the legend's field, not a hardcoded metadata field.
    expect(JSON.stringify(fill.paint!["fill-color"])).toContain('"get","income"');
  });

  it("applies the no-data guard when legend_spec declares nodata", () => {
    const l = layer({
      legend_spec: {
        type: "graduated", field: "pop", breaks: [0, 10, 20], palette: "YlOrRd",
        palette_colors: ["#a", "#b"], nodata: { color: "#ccc", label: "No data" },
      } as any,
    });
    const spec = hudStateToMapSpec({ layers: [l], processLayers: {}, activeFilters: {}, is3D: false });
    const fill = spec.layers.find((x) => x.id.endsWith("__fill"))!;
    const expr = fill.paint!["fill-color"] as unknown as unknown[];
    expect(expr[0]).toBe("case");
    expect(JSON.stringify(expr)).toContain('"#ccc"');
  });

  it("non-thematic layer keeps the legacy fill_color coalesce + flat fallback", () => {
    const l = layer({ style: { color: "#ff0000" } }); // no legend_spec
    const spec = hudStateToMapSpec({ layers: [l], processLayers: {}, activeFilters: {}, is3D: false });
    const fill = spec.layers.find((x) => x.id.endsWith("__fill"))!;
    // byte-identical to the pre-refactor behavior.
    expect(fill.paint!["fill-color"]).toEqual(["coalesce", ["get", "fill_color"], "#ff0000"]);
  });

  it("categorical legend_spec emits a match expression on the live map", () => {
    const l = layer({
      legend_spec: {
        type: "categorical", field: "landuse", categories: [
          { key: "residential", color: "#0", label: "Res" },
          { key: "commercial", color: "#1", label: "Com" },
        ],
      } as any,
    });
    const spec = hudStateToMapSpec({ layers: [l], processLayers: {}, activeFilters: {}, is3D: false });
    const fill = spec.layers.find((x) => x.id.endsWith("__fill"))!;
    expect(fill.paint!["fill-color"]).toEqual([
      "match", ["get", "landuse"], "residential", "#0", "commercial", "#1", "#1",
    ]);
  });

  it("legend filter range uses the legend field (single identity)", () => {
    const l = layer({
      legend_spec: {
        type: "graduated", field: "score", breaks: [0, 50, 100], palette: "YlOrRd",
        palette_colors: ["#a", "#b"],
      } as any,
    });
    const spec = hudStateToMapSpec({
      layers: [l], processLayers: {},
      activeFilters: { "thematic-1": [[0, 50]] }, is3D: false,
    });
    const fill = spec.layers.find((x) => x.id.endsWith("__fill"))!;
    const filter = JSON.stringify(fill.filter);
    // The range filter references the legend field 'score', proving paint and
    // filter share one field identity.
    expect(filter).toContain('"get","score"');
    expect(filter).toContain('"score"');
  });

  it("thematic derivation is O(classes): a 50k-feature layer produces ONE step expression", () => {
    // Build a large feature collection; the adapter must NOT scan per-feature
    // to derive the thematic color (it reads legend_spec, O(1) per layer).
    const big = polyFeatures(1);
    big.features = Array.from({ length: 50000 }, (_, i) => ({
      type: "Feature" as const,
      geometry: { type: "Polygon" as const, coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
      properties: { pop: i },
    }));
    const l = layer({
      id: "big", source: big,
      legend_spec: {
        type: "graduated", field: "pop", breaks: [0, 10000, 20000, 40000], palette: "YlOrRd",
        palette_colors: ["#a", "#b", "#c"],
      } as any,
    });
    const t0 = Date.now();
    const spec = hudStateToMapSpec({ layers: [l], processLayers: {}, activeFilters: {}, is3D: false });
    const dt = Date.now() - t0;
    const fill = spec.layers.find((x) => x.id === "big__fill")!;
    // The color expression is a single step (3 classes), independent of 50k features.
    expect((fill.paint!["fill-color"] as unknown as unknown[])[0]).toBe("step");
    // Guard: must complete quickly — thematic derivation must not be O(features).
    // (Geometry introspection is pre-existing O(features) and allowed; this
    // bounds total wall-clock as a coarse sanity check.)
    expect(dt).toBeLessThan(4000);
  });
});
