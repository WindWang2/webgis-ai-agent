import { describe, it, expect } from "vitest";
import { computeInteractiveIds, resolveParentLayerId } from "./interactive-ids";
import type { MapSpec } from "@/lib/mapspec-compiler/types";

// FE-3 (design §7): interactive-layer-id derivation + longest-prefix
// attribution. Pure functions — unit-level coverage for the registry-vs-fallback
// rule and the poi/poi_schools mis-attribution fix.

function specOf(layerIds: string[]): MapSpec {
  return {
    version: "1.0",
    sources: {},
    layers: layerIds.map((id) => ({ id, source: "s", type: "circle" as const, paint: {} })),
  };
}

describe("computeInteractiveIds — applied-spec registry vs style-scan fallback", () => {
  it("derives sublayer ids from the applied spec when present", () => {
    const spec = specOf(["poi__point", "poi__fill", "base-raster__main"]);
    expect(computeInteractiveIds(spec, [{ id: "stale-layer__point" }])).toEqual([
      "poi__point",
      "poi__fill",
      "base-raster__main",
    ]);
  });

  it("ignores non-sublayer ids from the applied spec (no `__` separator)", () => {
    const spec = specOf(["poi__point", "background", "custom-heatmap"]);
    expect(computeInteractiveIds(spec, [])).toEqual(["poi__point"]);
  });

  it("falls back to scanning the live style when appliedSpec is null", () => {
    const styleLayers = [{ id: "poi_schools__point" }, { id: "background" }, { id: "poi__circle" }];
    expect(computeInteractiveIds(null, styleLayers)).toEqual(["poi_schools__point", "poi__circle"]);
  });

  it("returns [] when both sources are empty", () => {
    expect(computeInteractiveIds(null, [])).toEqual([]);
    expect(computeInteractiveIds(specOf([]), [])).toEqual([]);
  });
});

describe("resolveParentLayerId — longest-prefix parent attribution", () => {
  it("picks the LONGEST matching project layer id (poi vs poi_schools)", () => {
    // poi_schools must win even when `poi` comes first in the array — the
    // naive first-prefix-match would mis-attribute (findings D).
    expect(resolveParentLayerId("poi_schools__point", ["poi", "poi_schools"])).toBe("poi_schools");
    expect(resolveParentLayerId("poi__point", ["poi", "poi_schools"])).toBe("poi");
  });

  it("matches any sublayer suffix of the same parent", () => {
    expect(resolveParentLayerId("ref:geojson-abc__fill", ["ref:geojson-abc"])).toBe("ref:geojson-abc");
    expect(resolveParentLayerId("ref:geojson-abc__line", ["ref:geojson-abc"])).toBe("ref:geojson-abc");
    expect(resolveParentLayerId("ref:geojson-abc__point", ["ref:geojson-abc"])).toBe("ref:geojson-abc");
  });

  it("requires the `__` separator — a prefix without it does not own the sublayer", () => {
    // 'poi' must NOT own 'poi-extra__point' (dash separator, different layer).
    expect(resolveParentLayerId("poi-extra__point", ["poi"])).toBeUndefined();
  });

  it("returns undefined when no project layer owns the sublayer (process-* overlays)", () => {
    expect(resolveParentLayerId("process-step1__fill", ["poi", "poi_schools"])).toBeUndefined();
    expect(resolveParentLayerId("unknown__point", ["poi"])).toBeUndefined();
  });

  it("handles an empty layer id list", () => {
    expect(resolveParentLayerId("poi__point", [])).toBeUndefined();
  });
});
