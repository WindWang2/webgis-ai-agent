import { describe, it, expect } from "vitest";
import { sanitizeMapLibreExpression, toMapLibrePaint } from "@/lib/mapspec-runtime/paint-bridge";
import { compileStyleMethod } from "@/lib/mapspec-compiler/compiler";
import type { MapSpecLayer } from "@/lib/mapspec-compiler/types";

/**
 * Paint 方言桥契约(paint-bridge.ts)。后端 MapSpec 用规范短键
 * (`color`/`radius`/…,值可为 StyleMethod 对象),adapter 用 MapLibre 原生键;
 * addLayerSafe 直传 MapLibre 前必须在此汇合 —— 规范键按类型降级、原生键
 * 直通、未知键丢弃(而非以 `unknown property "color"` 被 MapLibre 拒绝)。
 */
function layer(type: MapSpecLayer["type"], paint: Record<string, unknown>): MapSpecLayer {
  return { id: "L", source: "S", type, paint } as unknown as MapSpecLayer;
}

describe("toMapLibrePaint — 后端规范键按图层类型降级", () => {
  it("circle: color/radius → circle-color/circle-radius(converter DEFAULT_CONSTANT_PAINTS 形)", () => {
    expect(toMapLibrePaint(layer("circle", { color: "#3b82f6", radius: 5 }))).toEqual({
      "circle-color": "#3b82f6",
      "circle-radius": 5,
    });
  });

  it("fill: color/opacity → fill-color/fill-opacity", () => {
    expect(toMapLibrePaint(layer("fill", { color: "#3b82f6", opacity: 0.6 }))).toEqual({
      "fill-color": "#3b82f6",
      "fill-opacity": 0.6,
    });
  });

  it("line: color/width → line-color/line-width", () => {
    expect(toMapLibrePaint(layer("line", { color: "#2563eb", width: 2 }))).toEqual({
      "line-color": "#2563eb",
      "line-width": 2,
    });
  });

  it("raster: opacity → raster-opacity", () => {
    expect(toMapLibrePaint(layer("raster", { opacity: 0.85 }))).toEqual({
      "raster-opacity": 0.85,
    });
  });

  it("StyleMethod 值经 compileStyleMethod 降级,与 headless 编译器同形", () => {
    const step = { method: "step", field: "pop", default: "#ffffb2", stops: [[10, "#fd8d3c"]] } as unknown as Parameters<typeof compileStyleMethod>[0];
    const out = toMapLibrePaint(layer("fill", { color: step }));
    expect(out["fill-color"]).toEqual(compileStyleMethod(step));
    expect(out["fill-color"]).toEqual([
      "step", ["to-number", ["get", "pop"]], "#ffffb2", 10, "#fd8d3c",
    ]);
  });

  it("heatmap: 规范 radius/opacity 降级,raw hex color → 密度 ramp(compiler 同款)", () => {
    const out = toMapLibrePaint(layer("heatmap", { color: "#d97706", radius: 10, opacity: 0.9 }));
    expect(out["heatmap-radius"]).toBe(10);
    expect(out["heatmap-opacity"]).toBe(0.9);
    expect(out["heatmap-color"]).toEqual([
      "interpolate", ["linear"], ["heatmap-density"],
      0, "rgba(0,0,0,0)", 0.1, "#d97706", 1, "#d97706",
    ]);
  });
});

describe("toMapLibrePaint — adapter 原生键直通", () => {
  it("fill: fill-color/fill-outline-color 原样保留", () => {
    const paint = {
      "fill-color": "rgba(22, 163, 74, 0.08)",
      "fill-outline-color": "rgba(22, 163, 74, 0.3)",
    };
    expect(toMapLibrePaint(layer("fill", paint))).toEqual(paint);
  });

  it("line: line-dasharray 等原生表达式直通", () => {
    const paint = { "line-color": "#16a34a", "line-width": 1.5, "line-dasharray": [3, 3] };
    expect(toMapLibrePaint(layer("line", paint))).toEqual(paint);
  });

  it("converter 热力图原生 heatmap-* 表达式直通(zoom 插值 radius/密度色带)", () => {
    const paint = {
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 15, 12, 30],
      "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(0,0,0,0)"],
    };
    expect(toMapLibrePaint(layer("heatmap", paint))).toEqual(paint);
  });

  it("symbol: text-*/icon-* 命名空间直通(无 symbol-* 前缀)", () => {
    const paint = { "text-color": "#0f172a", "text-halo-width": 1.5, "icon-color": "#fff" };
    expect(toMapLibrePaint(layer("symbol", paint))).toEqual(paint);
  });

  it("raster-resampling 直通(raster paint 命名空间)", () => {
    const paint = { "raster-opacity": 0.85, "raster-resampling": "linear" };
    expect(toMapLibrePaint(layer("raster", paint))).toEqual(paint);
  });
});

describe("toMapLibrePaint — 冲突与防御", () => {
  it("规范键优先于同目标的原生键(compiler 优先级语义)", () => {
    expect(
      toMapLibrePaint(layer("circle", { color: "#111111", "circle-color": "#222222" })),
    ).toEqual({ "circle-color": "#111111" });
  });

  it("live-spec applyPending 双写(opacity 规范键 + 原生键)不重复、值一致", () => {
    expect(
      toMapLibrePaint(layer("fill", { opacity: 0.6, "fill-opacity": 0.6 })),
    ).toEqual({ "fill-opacity": 0.6 });
  });

  it("无法映射的键被丢弃:paint.color 不再以 unknown property 到达 MapLibre", () => {
    // symbol 无 color 规范映射 —— 正是线上报错的形态(键留下、值丢弃)。
    expect(toMapLibrePaint(layer("symbol", { color: "#ff0000", "text-color": "#000" }))).toEqual({
      "text-color": "#000",
    });
    expect(toMapLibrePaint(layer("fill", { colour: "#typo" }))).toEqual({});
  });

  it("缺失/空 paint 与未知图层类型安全返回", () => {
    expect(toMapLibrePaint({ id: "L", source: "S", type: "fill" } as MapSpecLayer)).toEqual({});
    expect(toMapLibrePaint(layer("fill", {}))).toEqual({});
    expect(toMapLibrePaint(layer("circle", { color: "#fff" }))).toEqual({
      "circle-color": "#fff",
    });
  });
});

describe("sanitizeMapLibreExpression — MapLibre arity", () => {
  it("appends fallback to a case expression missing the otherwise arm", () => {
    // Pi/agent fill-color often: ["case", cond, color] (length 3, odd).
    expect(
      sanitizeMapLibreExpression([
        "case",
        ["==", ["get", "lisa_cluster"], "HH"],
        "#ff0000",
      ]),
    ).toEqual([
      "case",
      ["==", ["get", "lisa_cluster"], "HH"],
      "#ff0000",
      "#cccccc",
    ]);
  });

  it("appends fallback to a match expression missing default (even length)", () => {
    expect(
      sanitizeMapLibreExpression(["match", ["get", "cluster"], "HH", "#ff0000", "LL", "#0000ff"]),
    ).toEqual(["match", ["get", "cluster"], "HH", "#ff0000", "LL", "#0000ff", "#cccccc"]);
  });

  it("leaves a valid LISA match untouched", () => {
    const expr = ["match", ["get", "lisa_cluster"], "HH", "#ff0000", "NS", "#ccc", "#ccc"];
    expect(sanitizeMapLibreExpression(expr)).toEqual(expr);
  });

  it("toMapLibrePaint sanitizes native fill-color case before addLayer", () => {
    const out = toMapLibrePaint(
      layer("fill", {
        "fill-color": ["case", ["==", ["get", "lisa_cluster"], "HH"], "#ff0000"],
      }),
    );
    expect(out["fill-color"]).toEqual([
      "case",
      ["==", ["get", "lisa_cluster"], "HH"],
      "#ff0000",
      "#cccccc",
    ]);
  });
});
