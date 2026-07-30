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
      expect(compileStyleMethod({ type: "constant", value: 12 })).toBe(12);
    });

    it("handles field style method", () => {
      expect(compileStyleMethod({ type: "field", field: "magnitude" })).toEqual([
        "get",
        "magnitude",
      ]);
    });

    it("compiles interpolate style method", () => {
      const compiled = compileStyleMethod({
        type: "interpolate",
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

    it("compiles step style method", () => {
      const compiled = compileStyleMethod({
        type: "step",
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
        type: "match",
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
                type: "interpolate",
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
                type: "interpolate",
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
                type: "interpolate",
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
