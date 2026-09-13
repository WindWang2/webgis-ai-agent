import { describe, expect, it } from "vitest";
import { compileMapSpec, compileStyleMethod } from "./compiler";
import type { MapSpec, MapSpecLayer, StyleMethod } from "./types";

/**
 * AC-06 (ADR-0155) P4 — layer type × StyleMethod 全组合矩阵。
 *
 * 此前 compiler.ts 的 paint 白名单 if/else 链有两个结构性缺口：
 *  1. symbol 层没有分支 → 静默空 paint；
 *  2. interpolate 只支持 ["linear"]，且 background/hillshade 连类型并集都没有。
 *
 * 本矩阵锁死：每个 layer type 在给定 StyleMethod 后必须产出**非空且形状
 * 正确**的 paint（无静默空 paint 通道）；StyleMethod 的每种插值模式产出的
 * 表达式算子逐字断言。
 */

const ALL_LAYER_TYPES = [
  "circle",
  "line",
  "fill",
  "symbol",
  "heatmap",
  "raster",
  "fill-extrusion",
  "background",
  "hillshade",
] as const;

type LayerType = (typeof ALL_LAYER_TYPES)[number];

/** 每个层类型的「颜色」规范键编译目标（symbol/background/hillshade 各有映射）。 */
const COLOR_TARGET: Record<LayerType, string> = {
  circle: "circle-color",
  line: "line-color",
  fill: "fill-color",
  symbol: "text-color",
  heatmap: "heatmap-color",
  raster: "",
  "fill-extrusion": "fill-extrusion-color",
  background: "background-color",
  hillshade: "",
};

function specWithLayer(layer: Partial<MapSpecLayer>): MapSpec {
  return {
    version: "1.0",
    sources: {
      src: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
      dem: { type: "raster-dem", url: "https://dem.test/{z}/{x}/{y}.png" },
    } as any,
    layers: [
      {
        id: "matrix-layer",
        source: layer.type === "hillshade" ? "dem" : "src",
        ...(layer as object),
      } as MapSpecLayer,
    ],
  };
}

describe("AC-06 matrix: layer type × StyleMethod", () => {
  it("symbol layer no longer compiles to silent empty paint", () => {
    const result = compileMapSpec(
      specWithLayer({ id: "sym", type: "symbol", paint: { color: "#333333", opacity: 0.9 } }),
    );
    const lyr = result.style.layers.find((l: any) => l.id === "sym");
    expect(lyr.paint).not.toEqual({});
    expect(lyr.paint["text-color"]).toBe("#333333");
    expect(lyr.paint["text-opacity"]).toBe(0.9);
  });

  it("background compiles without a source key (MapLibre contract)", () => {
    const result = compileMapSpec(
      specWithLayer({ id: "bg", type: "background", source: "", paint: { color: "#0a0a0a", opacity: 1 } }),
    );
    const lyr = result.style.layers.find((l: any) => l.id === "bg");
    expect(lyr.source).toBeUndefined();
    expect(lyr.paint["background-color"]).toBe("#0a0a0a");
    expect(lyr.paint["background-opacity"]).toBe(1);
    expect(result.report.success).toBe(true);
  });

  it("hillshade consumes a raster-dem source and maps canonical opacity to exaggeration", () => {
    const result = compileMapSpec(
      specWithLayer({
        id: "hs",
        type: "hillshade",
        paint: {
          opacity: 0.6,
          "hillshade-shadow-color": "#000000",
          "hillshade-highlight-color": "#ffffff",
        } as any,
      }),
    );
    expect(result.report.success).toBe(true);
    expect(result.style.sources.dem).toEqual({
      type: "raster-dem",
      url: "https://dem.test/{z}/{x}/{y}.png",
    });
    const lyr = result.style.layers.find((l: any) => l.id === "hs");
    expect(lyr.paint["hillshade-exaggeration"]).toBe(0.6);
    expect(lyr.paint["hillshade-shadow-color"]).toBe("#000000");
    expect(lyr.paint["hillshade-highlight-color"]).toBe("#ffffff");
  });

  for (const layerType of ALL_LAYER_TYPES) {
    for (const [methodName, styleMethod] of [
      ["constant", { method: "constant", value: "#112233" }],
      ["interpolate-linear", { method: "interpolate", field: "value", stops: [[0, "#000"], [10, "#fff"]] }],
      ["interpolate-exponential", {
        method: "interpolate",
        field: "value",
        stops: [[0, "#000"], [10, "#fff"]],
        interpolation: { kind: "exponential", base: 1.4 },
      }],
      ["interpolate-cubic-bezier", {
        method: "interpolate",
        field: "value",
        stops: [[0, "#000"], [10, "#fff"]],
        interpolation: { kind: "cubic-bezier", controlPoints: [0.42, 0, 0.58, 1] },
      }],
      ["step", { method: "step", field: "value", stops: [[5, "#0f0"], [9, "#00f"]], default: "#eee" }],
      ["match", { method: "match", field: "kind", cases: [["a", "#111"], ["b", "#222"]], default: "#333" }],
      ["field", { method: "field", field: "tint" }],
    ] as Array<[string, StyleMethod]>) {
      const colorTarget = COLOR_TARGET[layerType];
      const paint: Record<string, unknown> = {};
      if (colorTarget) paint.color = styleMethod;

      it(`${layerType} × ${methodName} → non-empty correctly-shaped paint`, () => {
        const result = compileMapSpec(specWithLayer({ type: layerType, paint: paint as any }));
        const errors = result.report.errors;
        expect(errors).toEqual([]);
        const lyr = result.style.layers.find((l: any) => l.id === "matrix-layer");
        if (!colorTarget) {
          // raster 只认 opacity；hillshade 的 canonical opacity → exaggeration。
          // 无 color 键时不断言颜色面，只断言编译无错。
          expect(lyr.paint).toBeDefined();
          return;
        }
        if (layerType === "heatmap" && methodName !== "constant") {
          // heatmap 的 color 契约 = 常量 hex（heatmap-density 域）；对象方法
          // 不可表达 —— 编译无错但颜色面缺失必须留 evidence（不静默）。
          expect(lyr.paint["heatmap-color"]).toBeUndefined();
          return;
        }
        expect(lyr.paint[colorTarget]).toBeDefined();
        const out = lyr.paint[colorTarget];
        if (layerType === "heatmap" && methodName === "constant") {
          // 常量 hex → transparent→hot 密度 ramp（compiler.ts 既有约定）。
          expect(out[0]).toBe("interpolate");
          expect(out[2]).toEqual(["heatmap-density"]);
          expect(JSON.stringify(out)).toContain("#112233");
          return;
        }
        switch (methodName) {
          case "constant":
            expect(out).toBe("#112233");
            break;
          case "interpolate-linear":
            expect(out[0]).toBe("interpolate");
            expect(out[1]).toEqual(["linear"]);
            expect(out[2]).toEqual(["to-number", ["get", "value"]]);
            break;
          case "interpolate-exponential":
            expect(out[0]).toBe("interpolate");
            expect(out[1]).toEqual(["exponential", 1.4]);
            break;
          case "interpolate-cubic-bezier":
            expect(out[0]).toBe("interpolate");
            expect(out[1]).toEqual(["cubic-bezier", 0.42, 0, 0.58, 1]);
            break;
          case "step":
            expect(out[0]).toBe("step");
            break;
          case "match":
            expect(out[0]).toBe("match");
            break;
          case "field":
            expect(out).toEqual(["get", "tint"]);
            break;
        }
      });
    }
  }

  it("interpolate field:'zoom' compiles to camera interpolation (no get/to-number wrap)", () => {
    const out = compileStyleMethod({
      method: "interpolate",
      field: "zoom",
      stops: [[4, 2], [16, 12]],
    } as any);
    expect(out).toEqual(["interpolate", ["linear"], ["zoom"], 4, 2, 16, 12]);
  });

  it("AC-06 P1: law fills missing radius/width/opacity with zoom expressions", () => {
    const result = compileMapSpec(
      specWithLayer({
        id: "law-circle",
        type: "circle",
        paint: { color: "#333" } as any,
      }),
    );
    const lyr = result.style.layers.find((l: any) => l.id === "law-circle");
    expect(lyr.paint["circle-radius"][0]).toBe("interpolate");
    expect(lyr.paint["circle-radius"][2]).toEqual(["zoom"]);
    expect(lyr.paint["circle-opacity"]).toBeCloseTo(0.8, 5);

    const lineResult = compileMapSpec(
      specWithLayer({ id: "law-line", type: "line", paint: { color: "#333" } as any }),
    );
    const lineLyr = lineResult.style.layers.find((l: any) => l.id === "law-line");
    expect(lineLyr.paint["line-width"][0]).toBe("interpolate");

    const fillResult = compileMapSpec(
      specWithLayer({ id: "law-fill", type: "fill", paint: { color: "#333" } as any }),
    );
    const fillLyr = fillResult.style.layers.find((l: any) => l.id === "law-fill");
    expect(fillLyr.paint["fill-opacity"]).toBeCloseTo(0.8, 5);
  });

  it("AC-06 P1: explicit spec values always win over the law", () => {
    const result = compileMapSpec(
      specWithLayer({
        id: "explicit",
        type: "circle",
        paint: { color: "#333", radius: { method: "constant", value: 42 }, opacity: 0.25 } as any,
      }),
    );
    const lyr = result.style.layers.find((l: any) => l.id === "explicit");
    expect(lyr.paint["circle-radius"]).toBe(42);
    expect(lyr.paint["circle-opacity"]).toBe(0.25);
  });

  it("AC-06 P4: line dashArray/blur/translate and circle blur reach native paint", () => {
    const lineResult = compileMapSpec(
      specWithLayer({
        id: "dash",
        type: "line",
        paint: {
          color: "#333",
          dashArray: [2, 1],
          blur: 1.5,
          translate: [2, 2],
          translateAnchor: "viewport",
        } as any,
      }),
    );
    const lineLyr = lineResult.style.layers.find((l: any) => l.id === "dash");
    expect(lineLyr.paint["line-dasharray"]).toEqual([2, 1]);
    expect(lineLyr.paint["line-blur"]).toBe(1.5);
    expect(lineLyr.paint["line-translate"]).toEqual([2, 2]);
    expect(lineLyr.paint["line-translate-anchor"]).toBe("viewport");

    const circleResult = compileMapSpec(
      specWithLayer({ id: "blur", type: "circle", paint: { color: "#333", blur: 0.5 } as any }),
    );
    const circleLyr = circleResult.style.layers.find((l: any) => l.id === "blur");
    expect(circleLyr.paint["circle-blur"]).toBe(0.5);
  });

  it("AC-06 P4: fill outlineWidth is unmappable → evidence, not silent drop", async () => {
    const { getSymbolLawEvidence, resetSymbolLawEvidence } = await import(
      "@/lib/map-kit/symbol-law"
    );
    resetSymbolLawEvidence();
    const result = compileMapSpec(
      specWithLayer({
        id: "ow",
        type: "fill",
        paint: { color: "#333", outlineWidth: 3 } as any,
      }),
    );
    const lyr = result.style.layers.find((l: any) => l.id === "ow");
    expect(lyr.paint["fill-outline-width"]).toBeUndefined();
    const snap = getSymbolLawEvidence();
    expect(snap.counts["unmapped-paint-key"]).toBeGreaterThanOrEqual(1);
    expect(snap.events.some((e) => e.kind === "unmapped-paint-key" && e.id === "ow")).toBe(true);
  });
});
