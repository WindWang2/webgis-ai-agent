/**
 * Scene terrain compile projection tests (ADR-0199 M2).
 *
 * Oracle anchors:
 * - 旧 spec（无 scene）→ style 无 terrain 键（旧样式字节不变）。
 * - scene.terrain → MapLibre style terrain 投影（source + exaggeration）。
 * - 悬空 terrain 源 → 编译错误（fail-closed，与后端 validate 同口径）。
 */
import { describe, it, expect } from "vitest";
import { compileMapSpec } from "./compiler";
import { MapSpec } from "./types";

function baseSpec(): MapSpec {
  return {
    version: "1.4",
    view: { center: [116, 39], zoom: 10 },
    sources: {
      s1: {
        type: "geojson",
        inlineData: { type: "FeatureCollection", features: [] },
      } as any,
      dem: {
        type: "raster-dem",
        url: "https://example.test/dem/{z}/{x}/{y}.png",
        encoding: "terrarium",
      } as any,
    },
    layers: [{ id: "l1", source: "s1", type: "fill" } as any],
  } as MapSpec;
}

describe("scene terrain compile projection", () => {
  it("omits style.terrain for legacy specs without scene", () => {
    const result = compileMapSpec(baseSpec());
    expect((result.style as any).terrain).toBeUndefined();
  });

  it("projects scene.terrain into the style terrain object", () => {
    const spec = baseSpec();
    (spec as any).scene = {
      mode: "3d",
      terrain: { source: "dem", exaggeration: 1.5, vertical_unit: "m" },
    };
    const result = compileMapSpec(spec);
    expect((result.style as any).terrain).toEqual({
      source: "dem",
      exaggeration: 1.5,
    });
  });

  it("defaults exaggeration to 1.0 when omitted (honest scale)", () => {
    const spec = baseSpec();
    (spec as any).scene = { mode: "2.5d", terrain: { source: "dem" } };
    const result = compileMapSpec(spec);
    expect((result.style as any).terrain).toEqual({
      source: "dem",
      exaggeration: 1.0,
    });
  });

  it("fails closed on dangling terrain source", () => {
    const spec = baseSpec();
    (spec as any).scene = { mode: "3d", terrain: { source: "missing" } };
    const result = compileMapSpec(spec);
    expect((result.style as any).terrain).toBeUndefined();
    expect(result.report.errors.some((e) => e.code === "SCENE_TERRAIN_SOURCE_REF")).toBe(
      true,
    );
  });

  it("rejects terrain pointing at a non raster-dem source", () => {
    const spec = baseSpec();
    (spec as any).scene = { mode: "3d", terrain: { source: "s1" } };
    const result = compileMapSpec(spec);
    expect((result.style as any).terrain).toBeUndefined();
    expect(result.report.errors.some((e) => e.code === "SCENE_TERRAIN_SOURCE_TYPE")).toBe(
      true,
    );
  });

  it("does not emit terrain when only mode is set (no terrain object)", () => {
    const spec = baseSpec();
    (spec as any).scene = { mode: "3d" };
    const result = compileMapSpec(spec);
    expect((result.style as any).terrain).toBeUndefined();
    expect(result.report.success).toBe(true);
  });
});

describe("3D scene symbol alignment", () => {
  function specWithSymbol(sceneMode?: string): any {
    const spec: any = {
      version: "1.4",
      view: { center: [116, 39], zoom: 10 },
      sources: {
        s1: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
      },
      layers: [
        {
          id: "sym",
          source: "s1",
          type: "symbol",
          paint: { color: "#333" },
        },
      ],
    };
    if (sceneMode) spec.scene = { mode: sceneMode };
    return spec;
  }

  it("orients symbol icons to the viewport in 3d mode", () => {
    const result = compileMapSpec(specWithSymbol("3d"));
    const sym = result.style.layers.find((l: any) => l.id === "sym");
    expect(sym.layout["icon-pitch-alignment"]).toBe("viewport");
    expect(sym.layout["icon-rotation-alignment"]).toBe("viewport");
  });

  it("leaves symbol orientation untouched outside 3d mode", () => {
    const result = compileMapSpec(specWithSymbol("2d"));
    const sym = result.style.layers.find((l: any) => l.id === "sym");
    expect(sym.layout["icon-pitch-alignment"]).toBeUndefined();
  });

  it("never overrides an explicitly declared icon-pitch-alignment", () => {
    const spec = specWithSymbol("3d");
    spec.layers[0].layout = { "icon-pitch-alignment": "map" };
    const result = compileMapSpec(spec);
    const sym = result.style.layers.find((l: any) => l.id === "sym");
    expect(sym.layout["icon-pitch-alignment"]).toBe("map");
    // 未声明的 rotation 仍按场景默认补齐
    expect(sym.layout["icon-rotation-alignment"]).toBe("viewport");
  });
});
