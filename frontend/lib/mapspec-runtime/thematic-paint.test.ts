import { describe, it, expect } from "vitest";
import { legendSpecToColorExpression, thematicField, isThematic } from "@/lib/mapspec-runtime/thematic-paint";
import { compileStyleMethod } from "@/lib/mapspec-compiler/compiler";
import type { LegendSpec } from "@/lib/map-kit/types";

/**
 * ADR-0052 frontend thematic-paint contract. These pin the live-path
 * projection of legend_spec → MapLibre color expression, and assert it mirrors
 * the backend `spec_to_paint` + the compiler's `compileStyleMethod` so the live
 * map and the legend cannot diverge.
 */
describe("legendSpecToColorExpression — graduated → step", () => {
  it("builds a step over the field with default = palette_colors[0]", () => {
    const spec = {
      type: "graduated", field: "population",
      breaks: [0, 10, 20, 30], palette: "YlOrRd",
      palette_colors: ["#ffffb2", "#fd8d3c", "#bd0026"],
    } as LegendSpec;
    expect(legendSpecToColorExpression(spec)).toEqual([
      "step", ["to-number", ["get", "population"]], "#ffffb2",
      10, "#fd8d3c", 20, "#bd0026",
    ]);
  });

  it("returns null when breaks/colors are insufficient", () => {
    expect(legendSpecToColorExpression({ type: "graduated", field: "x", breaks: [1], palette_colors: [] } as any)).toBeNull();
  });
});

describe("legendSpecToColorExpression — continuous/divergent → interpolate", () => {
  it("builds an interpolate with evenly-spaced stops min→max", () => {
    const spec = {
      type: "continuous", field: "density", min: 0, max: 100, palette: "Blues",
      palette_colors: ["#eff3ff", "#6baed6", "#08519c"],
    } as LegendSpec;
    expect(legendSpecToColorExpression(spec)).toEqual([
      "interpolate", ["linear"], ["to-number", ["get", "density"]],
      0, "#eff3ff", 50, "#6baed6", 100, "#08519c",
    ]);
  });

  it("divergent interpolates the symmetric domain", () => {
    const spec = {
      type: "divergent", field: "dev", center: 0, min: -100, max: 100,
      palette: "Viridis", palette_colors: ["#1", "#2", "#3"],
    } as LegendSpec;
    expect(legendSpecToColorExpression(spec)).toEqual([
      "interpolate", ["linear"], ["to-number", ["get", "dev"]],
      -100, "#1", 0, "#2", 100, "#3",
    ]);
  });
});

describe("legendSpecToColorExpression — categorical → match", () => {
  it("builds a match with default = last category color", () => {
    const spec = {
      type: "categorical", field: "landuse", categories: [
        { key: "residential", color: "#0", label: "Res" },
        { key: "commercial", color: "#1", label: "Com" },
      ],
    } as LegendSpec;
    expect(legendSpecToColorExpression(spec)).toEqual([
      "match", ["get", "landuse"], "residential", "#0", "commercial", "#1", "#1",
    ]);
  });
});

describe("no-data guard", () => {
  it("wraps numeric expressions so null/missing → nodata color", () => {
    const spec = {
      type: "graduated", field: "pop", breaks: [0, 10, 20], palette: "YlOrRd",
      palette_colors: ["#a", "#b"], nodata: { color: "#cccccc", label: "No data" },
    } as any;
    expect(legendSpecToColorExpression(spec)).toEqual([
      "case", ["==", ["get", "pop"], null], "#cccccc",
      ["step", ["to-number", ["get", "pop"]], "#a", 10, "#b"],
    ]);
  });

  it("does not guard categorical (match default absorbs unmatched/null)", () => {
    const spec = {
      type: "categorical", field: "u", categories: [{ key: "a", color: "#0", label: "a" }],
      nodata: { color: "#ccc", label: "nd" },
    } as any;
    expect(legendSpecToColorExpression(spec)).toEqual([
      "match", ["get", "u"], "a", "#0", "#0",
    ]);
  });
});

describe("field identity + validity", () => {
  it("thematicField reads the canonical field", () => {
    expect(thematicField({ type: "graduated", field: "pop", breaks: [], palette_colors: [] } as any)).toBe("pop");
    expect(thematicField({ type: "continuous", min: 0, max: 1, palette: "Blues", palette_colors: [] } as any)).toBeNull();
  });
  it("isThematic is true for known modes with a field, false otherwise", () => {
    expect(isThematic({ type: "graduated", field: "x", breaks: [0, 1], palette_colors: ["#a"] } as any)).toBe(true);
    expect(isThematic({ type: "continuous", min: 0, max: 1, palette: "Blues", palette_colors: ["#a"] } as any)).toBe(false); // no field
    expect(isThematic({ type: "unknown", field: "x" } as any)).toBe(false); // unknown mode
    expect(isThematic(null)).toBe(false);
  });
  it("returns null for absent/invalid spec", () => {
    expect(legendSpecToColorExpression(null)).toBeNull();
    expect(legendSpecToColorExpression({ type: "unknown" } as any)).toBeNull();
  });
});

describe("ADR-0052 cross-check: legend_spec→expression matches compileStyleMethod", () => {
  // Pins that the live direct projection (legendSpecToColorExpression) and the
  // headless compiler lowering (compileStyleMethod of the StyleMethod that
  // spec_to_paint would emit) agree mode-for-mode. A future change to either
  // path that reintroduces live-vs-export drift fails here.
  it("graduated: direct step == compileStyleMethod(step)", () => {
    
    const spec = { type: "graduated", field: "pop", breaks: [0, 10, 20, 30], palette_colors: ["#a", "#b", "#c"] } as any;
    const styleMethod = { method: "step", field: "pop", default: "#a", stops: [[10, "#b"], [20, "#c"]] };
    expect(legendSpecToColorExpression(spec)).toEqual(compileStyleMethod(styleMethod));
  });
  it("continuous: direct interpolate == compileStyleMethod(interpolate)", () => {
    
    const spec = { type: "continuous", field: "d", min: 0, max: 100, palette_colors: ["#1", "#2", "#3"] } as any;
    const styleMethod = { method: "interpolate", field: "d", stops: [[0, "#1"], [50, "#2"], [100, "#3"]] };
    expect(legendSpecToColorExpression(spec)).toEqual(compileStyleMethod(styleMethod));
  });
  it("categorical: direct match == compileStyleMethod(match)", () => {
    
    const spec = { type: "categorical", field: "u", categories: [{ key: "a", color: "#0", label: "a" }, { key: "b", color: "#1", label: "b" }] } as any;
    const styleMethod = { method: "match", field: "u", cases: [["a", "#0"], ["b", "#1"]], default: "#1" };
    expect(legendSpecToColorExpression(spec)).toEqual(compileStyleMethod(styleMethod));
  });
});
