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

    it("emits fill-extrusion natively when layer.type === 'fill-extrusion' regardless of is3D (ADR-0095)", () => {
      const extLayer = baseLayer({
        id: "pop-3d",
        type: "fill-extrusion" as any,
        paint: {
          "fill-extrusion-color": "#ea580c",
          "fill-extrusion-height": ["*", ["get", "pop"], 0.1],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.85,
        } as any,
        source: fc(polygonFeature()),
      });
      const spec = hudStateToMapSpec({ layers: [extLayer], processLayers: {}, activeFilters: {}, is3D: false });
      const extrusions = spec.layers.filter((l) => l.type === "fill-extrusion");
      expect(extrusions).toHaveLength(1);
      expect(extrusions[0].paint!["fill-extrusion-color"]).toBe("#ea580c");
      expect(extrusions[0].paint!["fill-extrusion-height"]).toEqual(["*", ["get", "pop"], 0.1]);
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

  describe("layer.filter (issue #393: imperative APPLY_LAYER_FILTER survives reconcile)", () => {
    it("ANDs the imperative filter with the $type base on every sublayer", () => {
      const layer = baseLayer({
        source: fc(polygonFeature(), lineFeature(), pointFeature()),
        filter: [">", ["get", "pop"], 1000],
      });
      const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });

      const fill = spec.layers.find((l) => l.id === `L1${SUBLAYER_SEP}fill`)!;
      const line = spec.layers.find((l) => l.id === `L1${SUBLAYER_SEP}line`)!;
      const point = spec.layers.find((l) => l.id === `L1${SUBLAYER_SEP}point`)!;
      expect(fill.filter).toEqual(["all", ["==", "$type", "Polygon"], [">", ["get", "pop"], 1000]]);
      expect(line.filter).toEqual(["all", ["==", "$type", "LineString"], [">", ["get", "pop"], 1000]]);
      expect(point.filter).toEqual(["all", ["==", "$type", "Point"], [">", ["get", "pop"], 1000]]);
    });

    it("combines the imperative filter with activeFilters range filters", () => {
      const src = fc(polygonFeature({ score: 10 })) as any;
      src.metadata = { field: "score" };
      const layer = baseLayer({
        source: src,
        filter: ["==", ["get", "name"], "mall"],
      });
      const spec = hudStateToMapSpec({
        layers: [layer],
        processLayers: {},
        activeFilters: { L1: [[0, 25]] },
        is3D: false,
      });
      const fill = spec.layers.find((l) => l.type === "fill")!;
      expect(fill.filter).toEqual([
        "all",
        ["==", "$type", "Polygon"],
        ["==", ["get", "name"], "mall"],
        ["any", ["all", [">=", ["get", "score"], 0], ["<", ["get", "score"], 25]]],
      ]);
    });

    it("emits no filter clause when layer.filter is null (cleared)", () => {
      const layer = baseLayer({ source: fc(polygonFeature()), filter: null });
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

describe("Data Plane — MVT vector tile source for large ref layers", () => {
  const tileUrl = "http://x/api/v1/layers/data/ref:geojson-abc/tiles/{z}/{x}/{y}.mvt?session_id=sess";

  function bigSource(n: number): any {
    return {
      type: "FeatureCollection",
      features: Array.from({ length: n }, (_, i) => pointFeature({ i })),
    };
  }

  it("emits a vector source when _tileUrl set and features exceed the threshold", () => {
    const layer = baseLayer({ source: bigSource(6000), _tileUrl: tileUrl });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    expect(spec.sources.L1).toEqual({ type: "vector", tiles: [tileUrl], minzoom: 1, maxzoom: 16 });
  });

  it("keeps the geojson inline path below the threshold", () => {
    const small = bigSource(100);
    const layer = baseLayer({ source: small, _tileUrl: tileUrl });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    expect(spec.sources.L1).toEqual({ type: "geojson", inlineData: small });
  });

  it("stays on geojson when _tileUrl is absent even for huge layers", () => {
    const big = bigSource(10000);
    const layer = baseLayer({ source: big });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    expect(spec.sources.L1).toEqual({ type: "geojson", inlineData: big });
  });
});

describe("native heatmap mode — #679 single color source", () => {
  const legendSpec = {
    type: "continuous" as const,
    min: 0,
    max: 1,
    palette: "YlOrRd",
    palette_colors: ["#428cd2", "#3dbce8", "#60d678", "#fae032", "#fa8c28", "#eb2828"],
  };

  it("rebuilds heatmap-color from layer.legend_spec.palette_colors (backend NATIVE_HEATMAP_COLORS source)", () => {
    const layer = baseLayer({
      type: "heatmap",
      source: fc(pointFeature({ weight: 0.7 })),
      legend_spec: legendSpec,
    });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    const heat = spec.layers.find((l) => l.type === "heatmap")!;
    expect(heat).toBeDefined();
    // 停靠点镜像后端 HEATMAP_STOP_POSITIONS；首停靠为首可见色的透明变体
    // （NATIVE_HEATMAP_COLORS[palette][0] 语义，避免低密度段向黑插值）
    expect(heat.paint!["heatmap-color"]).toEqual([
      "interpolate", ["linear"], ["heatmap-density"],
      0, "rgba(66,140,210,0)",
      0.12, "#428cd2",
      0.25, "#3dbce8",
      0.45, "#60d678",
      0.65, "#fae032",
      0.85, "#fa8c28",
      1.0, "#eb2828",
    ]);
    // weight 死 ramp（要素从不携带 weight）按后端 paint 语义改常量 1
    expect(heat.paint!["heatmap-weight"]).toBe(1);
    expect(heat.paint!["heatmap-opacity"]).toBe(0.9);
  });

  it("legacy fallback without legend_spec keeps the hardcoded cyan→red band and constant weight 1", () => {
    const layer = baseLayer({
      type: "heatmap",
      source: fc(pointFeature({ weight: 0.7 })),
    });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    const heat = spec.layers.find((l) => l.type === "heatmap")!;
    const color = heat.paint!["heatmap-color"] as unknown as unknown[];
    expect(color).toContain("rgba(0,242,255,0.3)");
    expect(color).not.toContain("#428cd2");
    expect(heat.paint!["heatmap-weight"]).toBe(1);
  });

  it("radius contract: style.radius_px preferred; legacy meters value never pixels", () => {
    // 显式 radius_px（后端 dispatch 投影）→ 直通 clamp [4,80]
    const explicit = baseLayer({
      type: "heatmap",
      source: fc(pointFeature({ weight: 0.7 })),
      legend_spec: legendSpec,
      style: { renderType: "heatmap", radius_px: 22 },
    });
    const spec1 = hudStateToMapSpec({ layers: [explicit], processLayers: {}, activeFilters: {}, is3D: false });
    const r1 = spec1.layers.find((l) => l.type === "heatmap")!.paint!["heatmap-radius"] as unknown as unknown[];
    expect(r1).toEqual(["interpolate", ["linear"], ["zoom"], 0, 2, 9, 22, 13, 37]);

    // legacy style.radius=1500（米制残留）→ 超出 px 窗口 [4,100]，回落契约
    // 默认 30px（绝不 1500px；与后端 heatmap_contract 归一化阈值族一致）
    const layer = baseLayer({
      type: "heatmap",
      source: fc(pointFeature({ weight: 0.7 })),
      legend_spec: legendSpec,
      style: { renderType: "heatmap", radius: 1500 },
    });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    const heat = spec.layers.find((l) => l.type === "heatmap")!;
    const radius = heat.paint!["heatmap-radius"] as unknown as unknown[];
    expect(radius).toEqual(["interpolate", ["linear"], ["zoom"], 0, 2, 9, 30, 13, 51]);

    // legacy 面板/模板 px 值（4-100）直通
    const pxLayer = baseLayer({
      type: "heatmap",
      source: fc(pointFeature({ weight: 0.7 })),
      legend_spec: legendSpec,
      style: { renderType: "heatmap", radius: 25 },
    });
    const spec3 = hudStateToMapSpec({ layers: [pxLayer], processLayers: {}, activeFilters: {}, is3D: false });
    const r3 = spec3.layers.find((l) => l.type === "heatmap")!.paint!["heatmap-radius"] as unknown as unknown[];
    expect(r3[6]).toBe(25);
  });
});

describe("heatgrid mode — #679 single color source", () => {
  it("derives fill-color stops from legend_spec.palette_colors (weight-driven, transparent head)", () => {
    const layer = baseLayer({
      style: { color: "#16a34a", strokeColor: "#16a34a", renderType: "heatmap" },
      source: fc(polygonFeature({ weight: 0.5 })),
      legend_spec: {
        type: "continuous",
        min: 0,
        max: 1,
        palette: "YlOrRd",
        palette_colors: ["#428cd2", "#3dbce8", "#60d678", "#fae032", "#fa8c28", "#eb2828"],
      },
    });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    const fills = spec.layers.filter((l) => l.type === "fill");
    expect(fills).toHaveLength(1);
    expect(fills[0].paint!["fill-color"]).toEqual([
      "interpolate", ["linear"], ["get", "weight"],
      0, "rgba(66,140,210,0)",
      0.167, "#3dbce8",
      0.333, "#60d678",
      0.5, "#fae032",
      0.667, "#fa8c28",
      1.0, "#eb2828",
    ]);
  });

  it("legacy fallback without legend_spec keeps the hardcoded heatgrid band", () => {
    const layer = baseLayer({
      style: { color: "#16a34a", strokeColor: "#16a34a", renderType: "heatmap" },
      source: fc(polygonFeature({ weight: 0.5 })),
    });
    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    const fills = spec.layers.filter((l) => l.type === "fill");
    const color = fills[0].paint!["fill-color"] as unknown as unknown[];
    expect(color).toContain("rgba(0,242,255,0.4)");
  });
});
