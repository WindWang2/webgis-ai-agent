import { describe, it, expect } from "vitest";
import {
  compileMapSpec,
  compileStyleMethod,
  validateMapSpec,
  diffLiveStateVsMapSpec,
} from "./compiler";
import { MapSpec, SpatialMetaProfile } from "./types";

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

  describe("diffLiveStateVsMapSpec", () => {
    it("detects view zoom and center divergence", () => {
      const spec: MapSpec = {
        version: "1.0",
        view: { center: [100, 30], zoom: 5 },
        sources: {},
        layers: [],
      };

      const liveStateSame = { center: [100, 30], zoom: 5 };
      expect(diffLiveStateVsMapSpec(liveStateSame, spec).diverged).toBe(false);

      const liveStateMoved = { center: [105, 32], zoom: 7 };
      const diffResult = diffLiveStateVsMapSpec(liveStateMoved, spec);
      expect(diffResult.diverged).toBe(true);
      expect(diffResult.diffs).toHaveLength(2);
    });
  });
});
