import { describe, it, expect } from "vitest";
import { hudStateToMapSpec } from "./adapter";
import type { Layer } from "@/lib/types/layer";
import type { GeoJSONFeatureCollection, HeatmapRasterSource } from "@/lib/types";

// ADR-0036: hudStateToMapSpec is the pure adapter that flattens the HUD store's
// Layer[] (with conditional multi-sublayer fan-out driven by geometry
// introspection) into a flat MapSpec. It must produce byte-identical MapLibre
// paint/filter/expressions to the inline code it replaces in map-panel.tsx —
// these tests pin that contract.

const SUBLAYER_SEP = "__"; // see adapter.ts

function pointFeature(props: Record<string, unknown> = {}): any {
  return { type: "Feature", properties: props, geometry: { type: "Point", coordinates: [0, 0] } };
}
function lineFeature(props: Record<string, unknown> = {}): any {
  return { type: "Feature", properties: props, geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] } };
}
function polygonFeature(props: Record<string, unknown> = {}): any {
  return { type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } };
}

function fc(...features: any[]): GeoJSONFeatureCollection {
  return { type: "FeatureCollection", features } as any;
}

function baseLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: "L1",
    name: "Layer 1",
    type: "vector",
    visible: true,
    opacity: 1,
    source: fc(pointFeature()),
    style: { color: "#16a34a", strokeColor: "#16a34a" },
    ...overrides,
  };
}

describe("MapSpec Runtime Adapter — hudStateToMapSpec (ADR-0036)", () => {
  describe("empty input", () => {
    it("produces an empty MapSpec", () => {
      const spec = hudStateToMapSpec({ layers: [], processLayers: {}, activeFilters: {}, is3D: false });
      expect(spec.layers).toEqual([]);
      expect(spec.sources).toEqual({});
    });
  });

  describe("point-only layer → circle sublayer", () => {
    it("emits one circle layer with the exact point paint", () => {
      const layer = baseLayer({ source: fc(pointFeature()) });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });

      expect(Object.keys(spec.sources)).toEqual(["L1"]);
      expect(spec.sources.L1).toEqual({ type: "geojson", inlineData: layer.source });

      expect(spec.layers).toHaveLength(1);
      const circle = spec.layers[0];
      expect(circle.id).toBe(`L1${SUBLAYER_SEP}point`);
      expect(circle.source).toBe("L1");
      expect(circle.type).toBe("circle");
      expect(circle.paint).toEqual({
        "circle-radius": 6,
        "circle-color": ["coalesce", ["get", "fill_color"], "#16a34a"],
        "circle-stroke-width": 1.5,
        "circle-stroke-color": "rgba(22, 163, 74, 0.3)",
        "circle-opacity": 1,
      });
      expect(circle.layout?.visibility).toBe("visible");
    });

    it("uses interpolated weight radius when features carry weight and no pointSize", () => {
      const layer = baseLayer({
        style: { color: "#16a34a", strokeColor: "#16a34a" },
        source: fc(pointFeature({ weight: 0.5 })),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const circle = spec.layers[0];
      expect(circle.paint!["circle-radius"]).toEqual([
        "interpolate", ["linear"], ["get", "weight"], 0, 4, 1, 8,
      ]);
    });

    it("honors style.pointSize over weight interpolation", () => {
      const layer = baseLayer({
        style: { color: "#16a34a", strokeColor: "#16a34a", pointSize: 9 },
        source: fc(pointFeature({ weight: 0.5 })),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      expect(spec.layers[0].paint!["circle-radius"]).toBe(9);
    });
  });

  describe("line features → line sublayer", () => {
    it("emits a line layer with the exact line paint", () => {
      const layer = baseLayer({
        style: { color: "#16a34a", strokeColor: "#0000ff", strokeWidth: 3 },
        source: fc(lineFeature()),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const line = spec.layers.find((l) => l.type === "line");
      expect(line).toBeDefined();
      expect(line!.paint).toEqual({
        "line-color": ["coalesce", ["get", "fill_color"], "#0000ff"],
        "line-width": 3,
        "line-opacity": 1,
      });
    });

    it("compiles dashArray into line-dasharray when not 'solid'", () => {
      const layer = baseLayer({
        style: { color: "#16a34a", strokeColor: "#0000ff", strokeWidth: 2, dashArray: "dashed" },
        source: fc(lineFeature()),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      expect(spec.layers[0].paint!["line-dasharray"]).toEqual([4, 2]);
    });
  });

  describe("polygon features (normal mode)", () => {
    const layer = baseLayer({
      style: { color: "#ff0000", strokeColor: "#00ff00", strokeWidth: 2 },
      source: fc(polygonFeature()),
    });

    it("emits fill + outline (no extrusion when is3D=false)", () => {
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const fills = spec.layers.filter((l) => l.type === "fill");
      const lines = spec.layers.filter((l) => l.type === "line");
      const extrusions = spec.layers.filter((l) => l.type === "fill-extrusion");
      expect(fills).toHaveLength(1);
      expect(lines).toHaveLength(1);
      expect(extrusions).toHaveLength(0);

      expect(fills[0].paint).toEqual({
        "fill-color": ["coalesce", ["get", "fill_color"], "#ff0000"],
        "fill-opacity": 0.3,
      });
      expect(lines[0].paint).toEqual({
        "line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], "#00ff00"],
        "line-width": 2,
        "line-opacity": 1,
      });
    });

    it("emits fill-extrusion when is3D=true", () => {
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: true });
      const extrusions = spec.layers.filter((l) => l.type === "fill-extrusion");
      expect(extrusions).toHaveLength(1);
      expect(extrusions[0].paint).toEqual({
        "fill-extrusion-color": "#ff0000",
        "fill-extrusion-height": ["coalesce", ["get", "height"], 20],
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 1,
      });
    });

    it("honors style.fill=false by making fill transparent", () => {
      const noFill = baseLayer({
        style: { color: "#ff0000", strokeColor: "#00ff00", strokeWidth: 2, fill: false },
        source: fc(polygonFeature()),
      });
      const spec = hudStateToMapSpec({ layers: [noFill], processLayers: {}, activeFilters: {}, is3D: false });
      const fill = spec.layers.find((l) => l.type === "fill")!;
      expect(fill.paint!["fill-color"]).toBe("rgba(0,0,0,0)");
      expect(fill.paint!["fill-opacity"]).toBe(0);
    });
  });

  describe("mixed-geometry layer fans out to multiple sublayers", () => {
    it("emits fill + outline(line) + line + circle for a source with all three geometry types", () => {
      // Mirrors map-panel.tsx: polygons emit fill + an `outline` line sublayer,
      // and line features emit a separate `line` sublayer. Both sublayers are
      // MapLibre type "line" but distinct (different filters/paint) — pinning
      // this prevents collapsing them into one and losing the outline.
      const layer = baseLayer({
        source: fc(polygonFeature(), lineFeature(), pointFeature()),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const ids = spec.layers.map((l) => l.id).sort();
      expect(ids).toEqual([
        `L1${SUBLAYER_SEP}fill`,
        `L1${SUBLAYER_SEP}line`,
        `L1${SUBLAYER_SEP}outline`,
        `L1${SUBLAYER_SEP}point`,
      ]);
      // All share the same source.
      expect(spec.layers.every((l) => l.source === "L1")).toBe(true);
      // The two line-type sublayers filter to different geometry types.
      const outline = spec.layers.find((l) => l.id === `L1${SUBLAYER_SEP}outline`)!;
      const line = spec.layers.find((l) => l.id === `L1${SUBLAYER_SEP}line`)!;
      expect(outline.filter).toEqual(["==", "$type", "Polygon"]);
      expect(line.filter).toEqual(["==", "$type", "LineString"]);
    });
  });

  describe("native heatmap mode", () => {
    it("emits a single native MapLibre heatmap layer with the fixed intensity/weight paint", () => {
      const layer = baseLayer({
        type: "heatmap",
        source: fc(pointFeature({ weight: 0.7 })),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const heat = spec.layers.find((l) => l.type === "heatmap");
      expect(heat).toBeDefined();
      expect(heat!.paint).toHaveProperty("heatmap-weight");
      expect(heat!.paint).toHaveProperty("heatmap-intensity");
      expect(heat!.paint).toHaveProperty("heatmap-color");
      expect(heat!.paint).toHaveProperty("heatmap-radius");
      expect(heat!.paint).toHaveProperty("heatmap-opacity");
    });

    it("does NOT emit separate fill/line/circle sublayers in native heatmap mode", () => {
      const layer = baseLayer({
        type: "heatmap",
        source: fc(pointFeature({ weight: 0.7 })),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      expect(spec.layers.filter((l) => ["fill", "line", "circle"].includes(l.type))).toEqual([]);
    });
  });

  describe("heatgrid mode (polygon + renderType heatmap/grid)", () => {
    it("emits a fill layer with the heatgrid interpolate paint", () => {
      const layer = baseLayer({
        style: { color: "#16a34a", strokeColor: "#16a34a", renderType: "heatmap" },
        source: fc(polygonFeature({ weight: 0.5 })),
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const fills = spec.layers.filter((l) => l.type === "fill");
      expect(fills).toHaveLength(1);
      expect(fills[0].paint!["fill-color"]).toEqual([
        "interpolate", ["linear"], ["get", "weight"],
        0.0, "rgba(0,0,0,0)",
        0.2, "rgba(0,242,255,0.4)",
        0.4, "rgba(0,255,65,0.6)",
        0.6, "rgba(255,255,0,0.7)",
        0.8, "rgba(255,95,0,0.85)",
        1.0, "rgba(255,45,85,0.95)",
      ]);
    });
  });

  describe("raster-image heatmap source (HeatmapRasterSource)", () => {
    it("emits a raster image source + raster layer", () => {
      const rasterSource: HeatmapRasterSource = {
        image: "https://example.com/heat.png",
        bbox: [100, 20, 101, 21],
      } as any;
      const layer = baseLayer({
        type: "heatmap",
        opacity: 0.85,
        source: rasterSource,
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });

      expect(spec.sources.L1).toEqual({
        type: "raster",
        imageRef: "https://example.com/heat.png",
        bounds: [100, 20, 101, 21],
      });
      const raster = spec.layers.find((l) => l.type === "raster");
      expect(raster).toBeDefined();
      expect(raster!.paint).toEqual({ "raster-opacity": 0.85, "raster-resampling": "linear" });
    });
  });

  describe("raster/tile layer (URL source)", () => {
    it("emits a raster tile source + raster layer with style adjustments", () => {
      const layer = baseLayer({
        type: "raster",
        opacity: 0.9,
        source: "https://tiles.example.com/{z}/{x}/{y}.png",
        style: { brightness: 0.5, contrast: 0.3, saturation: 0.2 },
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      expect(spec.sources.L1).toEqual({ type: "geojson", url: "https://tiles.example.com/{z}/{x}/{y}.png" });
      const raster = spec.layers.find((l) => l.type === "raster")!;
      expect(raster.paint).toEqual({
        "raster-opacity": 0.9,
        "raster-brightness-max": 0.5,
        "raster-contrast": 0.3,
        "raster-saturation": 0.2,
      });
    });
  });

  describe("activeFilters (legend range filters)", () => {
    it("compiles thematic field + ranges into a composite filter expression", () => {
      const layer = baseLayer({
        source: fc(polygonFeature({ score: 50 }), { metadata: { field: "score" } } as any),
      });
      // The thematic field is read from source.metadata.field (mirrors map-panel:204).
      const fcWithMeta = { type: "FeatureCollection", features: [polygonFeature({ score: 50 })], metadata: { field: "score" } } as any;
      const layerWithMeta = { ...layer, source: fcWithMeta };
      const spec = hudStateToMapSpec({
        layers: [layerWithMeta],
        processLayers: {},
        activeFilters: { L1: [[0, 25], [50, 100]] },
        is3D: false,
      });
      const fill = spec.layers.find((l) => l.type === "fill")!;
      expect(fill.filter).toEqual([
        "all",
        ["==", "$type", "Polygon"],
        ["any",
          ["all", [">=", ["get", "score"], 0], ["<", ["get", "score"], 25]],
          ["all", [">=", ["get", "score"], 50], ["<", ["get", "score"], 100]],
        ],
      ]);
    });

    it("emits a bare $type filter when no activeFilters/field present", () => {
      const layer = baseLayer({ source: fc(polygonFeature()) });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      const fill = spec.layers.find((l) => l.type === "fill")!;
      expect(fill.filter).toEqual(["==", "$type", "Polygon"]);
    });
  });

  describe("visibility", () => {
    it("marks all sublayers visibility:none when layer.visible=false", () => {
      const layer = baseLayer({ visible: false, source: fc(polygonFeature(), lineFeature(), pointFeature()) });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
      expect(spec.layers.length).toBeGreaterThan(0);
      expect(spec.layers.every((l) => l.layout?.visibility === "none")).toBe(true);
    });
  });

  describe("process layers", () => {
    it("emits a 3-layer stack (fill+line+point) per process step with the green process paint", () => {
      const processLayers = {
        step1: fc(polygonFeature(), pointFeature()),
      };
      const spec = hudStateToMapSpec({ layers: [], processLayers, activeFilters: {}, is3D: false });
      expect(spec.sources["process-step1"]).toBeDefined();
      const ids = spec.layers.map((l) => l.id).sort();
      expect(ids).toEqual([
        "process-step1__fill",
        "process-step1__line",
        "process-step1__point",
      ]);
      const fill = spec.layers.find((l) => l.id === "process-step1__fill")!;
      expect(fill.paint!["fill-color"]).toBe("rgba(22, 163, 74, 0.08)");
    });
  });
});
