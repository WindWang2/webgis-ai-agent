import { describe, it, expect } from "vitest";
import {
  compileMapSpec,
  compileStyleMethod,
  validateMapSpec,
} from "./compiler";
import { MapSpec } from "./types";

describe("MapSpec Compiler (Seam A)", () => {
  describe("compileStyleMethod", () => {
    it("handles primitive values and constant style method", () => {
      expect(compileStyleMethod("#ff0000")).toBe("#ff0000");
      expect(compileStyleMethod({ method: "constant", value: 12 })).toBe(12);
    });

    it("handles field style method", () => {
      expect(compileStyleMethod({ method: "field", field: "magnitude" })).toEqual([
        "get",
        "magnitude",
      ]);
    });

    it("compiles interpolate style method", () => {
      const compiled = compileStyleMethod({
        method: "interpolate",
        field: "magnitude",
        stops: [
          [2.0, "#00ff00"],
          [5.0, "#ffff00"],
          [8.0, "#ff0000"],
        ],
      });

      expect(compiled).toEqual([
        "interpolate",
        ["linear"],
        ["to-number", ["get", "magnitude"]],
        2.0,
        "#00ff00",
        5.0,
        "#ffff00",
        8.0,
        "#ff0000",
      ]);
    });

    it("compiles step style method with default and stops", () => {
      const compiled = compileStyleMethod({
        method: "step",
        field: "score",
        default: "#ffffb2",
        stops: [
          [10.0, "#fd8d3c"],
          [20.0, "#bd0026"],
        ],
      });

      expect(compiled).toEqual([
        "step",
        ["to-number", ["get", "score"]],
        "#ffffb2",
        10.0,
        "#fd8d3c",
        20.0,
        "#bd0026",
      ]);
    });

    it("compiles step style method", () => {
      const compiled = compileStyleMethod({
        method: "step",
        field: "depth",
        stops: [
          [10, "#blue"],
          [50, "#yellow"],
          [100, "#red"],
        ],
      });

      expect(compiled).toEqual([
        "step",
        ["to-number", ["get", "depth"]],
        "#blue",
        50,
        "#yellow",
        100,
        "#red",
      ]);
    });

    it("compiles match style method", () => {
      const compiled = compileStyleMethod({
        method: "match",
        field: "category",
        cases: [
          ["A", "#ff0000"],
          ["B", "#00ff00"],
        ],
        default: "#0000ff",
      });

      expect(compiled).toEqual([
        "match",
        ["get", "category"],
        "A",
        "#ff0000",
        "B",
        "#00ff00",
        "#0000ff",
      ]);
    });
  });

  describe("validateMapSpec", () => {
    it("detects missing sources", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {},
        layers: [],
      };
      const { errors } = validateMapSpec(spec);
      expect(errors.some((e) => e.code === "MISSING_SOURCES")).toBe(true);
    });

    it("rejects non-increasing stops", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {
          pts: { type: "geojson", url: "/data.json" },
        },
        layers: [
          {
            id: "layer1",
            source: "pts",
            type: "circle",
            paint: {
              color: {
                method: "interpolate",
                field: "val",
                stops: [
                  [10, "#ff0000"],
                  [5, "#00ff00"],
                ],
              },
            },
          },
        ],
      };
      const { errors } = validateMapSpec(spec);
      expect(errors.some((e) => e.code === "NON_INCREASING_STOPS")).toBe(true);
    });

    it("rejects stops count less than 2", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {
          pts: { type: "geojson", url: "/data.json" },
        },
        layers: [
          {
            id: "layer1",
            source: "pts",
            type: "circle",
            paint: {
              color: {
                method: "interpolate",
                field: "val",
                stops: [[10, "#ff0000"]],
              },
            },
          },
        ],
      };
      const { errors } = validateMapSpec(spec);
      expect(errors.some((e) => e.code === "INVALID_STOPS_COUNT")).toBe(true);
    });
  });

  describe("compileMapSpec", () => {
    it("compiles valid MapSpec into style.json, index.html, legend, and report", () => {
      const spec: MapSpec = {
        version: "1.0",
        view: { center: [120.1, 30.2], zoom: 10 },
        sources: {
          earthquakes: {
            type: "geojson",
            url: "/api/geojson/earthquakes.json",
          },
        },
        layers: [
          {
            id: "eq-points",
            source: "earthquakes",
            type: "circle",
            paint: {
              color: {
                method: "interpolate",
                field: "mag",
                stops: [
                  [2.0, "#00ff00"],
                  [5.0, "#ff0000"],
                ],
              },
              radius: 6,
            },
            label: {
              field: "title",
              size: 12,
              color: "#333333",
            },
          },
        ],
      };

      const result = compileMapSpec(spec);

      expect(result.report.success).toBe(true);
      expect(result.style.version).toBe(8);
      expect(result.style.center).toEqual([120.1, 30.2]);
      expect(result.style.zoom).toBe(10);
      expect(result.style.sources.earthquakes.data).toBe("/api/geojson/earthquakes.json");

      // Verify layers compilation (circle layer + generated label layer)
      expect(result.style.layers).toHaveLength(2);
      expect(result.style.layers[0].id).toBe("eq-points");
      expect(result.style.layers[0].type).toBe("circle");
      expect(result.style.layers[1].id).toBe("eq-points-label");
      expect(result.style.layers[1].type).toBe("symbol");
      expect(result.style.layers[1].layout["text-field"]).toEqual(["get", "title"]);

      // Verify legend
      expect(result.legend).toHaveLength(1);
      expect(result.legend[0].items).toHaveLength(2);
      expect(result.legend[0].items[0].label).toBe("mag: 2");

      // Verify HTML string
      expect(result.html).toContain("<!DOCTYPE html>");
      expect(result.html).toContain("maplibregl.Map");
    });

    // #214 Seam C: a layer carrying a converter-emitted interpolate paint
    // (continuous legend_spec → interpolate StyleMethod) compiles to a valid
    // MapLibre interpolation expression through the full layer path.
    it("compiles a converter-emitted interpolate paint to a MapLibre interpolation expression", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {
          density: { type: "geojson", url: "/api/geojson/density.json" },
        },
        layers: [
          {
            id: "kde-fill",
            source: "density",
            type: "fill",
            // Shape produced by _convert_continuous_legend: stops distributed
            // across [min, max] from palette_colors.
            paint: {
              color: {
                method: "interpolate",
                field: "density",
                stops: [
                  [0.0, "#440154"],
                  [25.0, "#21908c"],
                  [50.0, "#fde725"],
                ],
              },
            },
          },
        ],
      };

      const result = compileMapSpec(spec);
      expect(result.report.success).toBe(true);
      expect(result.style.layers[0].paint["fill-color"]).toEqual([
        "interpolate",
        ["linear"],
        ["to-number", ["get", "density"]],
        0.0,
        "#440154",
        25.0,
        "#21908c",
        50.0,
        "#fde725",
      ]);
    });

    // #214 Seam C: a layer carrying a converter-emitted match paint
    // (categorical legend_spec → match StyleMethod) compiles to a valid
    // MapLibre match expression through the full layer path.
    it("compiles a converter-emitted match paint to a MapLibre match expression", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {
          lisa: { type: "geojson", url: "/api/geojson/lisa.json" },
        },
        layers: [
          {
            id: "lisa-fill",
            source: "lisa",
            type: "fill",
            // Shape produced by _convert_categorical_legend: cases from
            // categories[].key/color, default = last category color.
            paint: {
              color: {
                method: "match",
                field: "cluster",
                cases: [
                  ["HH", "#ff0000"],
                  ["LL", "#0000ff"],
                  ["HL", "#ffaaaa"],
                ],
                default: "#cccccc",
              },
            },
          },
        ],
      };

      const result = compileMapSpec(spec);
      expect(result.report.success).toBe(true);
      expect(result.style.layers[0].paint["fill-color"]).toEqual([
        "match",
        ["get", "cluster"],
        "HH",
        "#ff0000",
        "LL",
        "#0000ff",
        "HL",
        "#ffaaaa",
        "#cccccc",
      ]);
    });
  });

  // ADR-0011: a `type:"raster"` source (imageRef + bounds) compiles to a
  // MapLibre `image` source with the 4 corner coordinates, and a `type:"raster"`
  // layer compiles to a MapLibre raster layer carrying raster-opacity. The
  // imageRef cursor is emitted verbatim as the url (the session-aware caller
  // rewrites it to the serving route — the compiler stays session-agnostic).
  describe("raster source compilation (ADR-0011)", () => {
    it("emits an image source with corner coordinates from bounds + a raster layer", () => {
      const spec: MapSpec = {
        version: "1.0",
        view: { center: [100.5, 20.5], zoom: 8 },
        sources: {
          ndvi: {
            type: "raster",
            imageRef: "ref:raster/ndvi_src",
            bounds: [100.0, 20.0, 101.0, 21.0],
            imageSize: [256, 256],
          },
        },
        layers: [
          {
            id: "ndvi-layer",
            source: "ndvi",
            type: "raster",
            paint: { opacity: 0.85 },
          },
        ],
      };

      const result = compileMapSpec(spec);

      expect(result.report.success).toBe(true);
      // image source with 4 corners in MapLibre's [TL, TR, BR, BL] order.
      expect(result.style.sources.ndvi).toEqual({
        type: "image",
        url: "ref:raster/ndvi_src",
        coordinates: [
          [100.0, 21.0], // top-left     (w, n)
          [101.0, 21.0], // top-right    (e, n)
          [101.0, 20.0], // bottom-right (e, s)
          [100.0, 20.0], // bottom-left  (w, s)
        ],
      });
      // raster layer with raster-opacity, no label layer generated.
      const rasterLayer = result.style.layers.find((l: any) => l.id === "ndvi-layer");
      expect(rasterLayer).toBeDefined();
      expect(rasterLayer.type).toBe("raster");
      expect(rasterLayer.paint["raster-opacity"]).toBe(0.85);
    });
  });

  // #697: vector source passthrough — replaces the empty-geojson placeholder
  describe("vector source compilation (#697)", () => {
    it("emits a vector source verbatim with tiles and default minzoom/maxzoom", () => {
      const spec: MapSpec = {
        version: "1.0",
        view: { center: [0, 5], zoom: 2 },
        sources: {
          pts: {
            type: "vector",
            tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"],
          } as any,
        },
        layers: [
          {
            id: "pts-layer",
            source: "pts",
            type: "circle",
            paint: { color: "#e41a1c", radius: 8 },
          },
        ],
      };
      const result = compileMapSpec(spec as any);
      expect(result.style.sources.pts).toEqual({
        type: "vector",
        tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"],
        minzoom: 0,
        maxzoom: 14,
      });
      // vector layer must carry source-layer (defaults to "data")
      const lyr = result.style.layers.find((l: any) => l.id === "pts-layer");
      expect(lyr["source-layer"]).toBe("data");
    });

    it("preserves explicit minzoom/maxzoom and explicit sourceLayer", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {
          v: {
            type: "vector",
            tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"],
            minzoom: 2,
            maxzoom: 8,
          } as any,
        },
        layers: [
          {
            id: "l1",
            source: "v",
            type: "circle",
            sourceLayer: "custom",
            paint: { color: "#ff0000" },
          } as any,
        ],
      };
      const result = compileMapSpec(spec as any);
      expect(result.style.sources.v.minzoom).toBe(2);
      expect(result.style.sources.v.maxzoom).toBe(8);
      const lyr = result.style.layers.find((l: any) => l.id === "l1");
      expect(lyr["source-layer"]).toBe("custom");
    });

    it("falls back to empty geojson only for genuinely unknown source types", () => {
      const spec: MapSpec = {
        version: "1.0",
        sources: {
          weird: { type: "unknown" } as any,
        },
        layers: [{ id: "l", source: "weird", type: "circle" } as any],
      };
      const result = compileMapSpec(spec as any);
      expect(result.style.sources.weird).toEqual({
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
    });
  });
});

describe("heatmap raw paint dialect bridge (GIS harness radius contract)", () => {
  it("passes through raw heatmap-* MapLibre expressions emitted by the dispatch authoring path", () => {
    // dispatch 授权链路产出的 heatmap 层带原生 zoom 插值 radius 表达式与
    // 密度色带 —— headless 编译此前只认高级键，授权层编译出空 paint。
    const radiusExpr = ["interpolate", ["linear"], ["zoom"], 0, 2, 9, 22, 13, 37];
    const spec: MapSpec = {
      version: "1.0",
      sources: { s1: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } } },
      layers: [{
        id: "heat", source: "s1", type: "heatmap",
        paint: {
          "heatmap-radius": radiusExpr,
          "heatmap-weight": 1,
          "heatmap-opacity": 0.9,
        } as any,
      }],
    };
    const result = compileMapSpec(spec);
    const lyr = result.style.layers.find((l: any) => l.id === "heat");
    expect(lyr.paint["heatmap-radius"]).toEqual(radiusExpr);
    expect(lyr.paint["heatmap-weight"]).toBe(1);
    expect(lyr.paint["heatmap-opacity"]).toBe(0.9);
  });

  it("high-level paint.radius still wins over the raw key", () => {
    const spec: MapSpec = {
      version: "1.0",
      sources: { s1: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } } },
      layers: [{
        id: "heat", source: "s1", type: "heatmap",
        paint: {
          radius: 18,
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 2, 9, 30, 13, 51],
        } as any,
      }],
    };
    const result = compileMapSpec(spec);
    const lyr = result.style.layers.find((l: any) => l.id === "heat");
    expect(lyr.paint["heatmap-radius"]).toBe(18);
  });
});
