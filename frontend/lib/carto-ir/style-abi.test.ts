import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  asExpression,
  asPaintValue,
  asLayerSpecification,
  asCompiledStyleView,
  type CompiledStyle,
  type SourceSpecification,
} from "./style-abi";

/**
 * C11 Renderer ABI 测试 —— MapSpec 开放 JSON → MapLibre 官方类型的
 * 单一 audited cast 边界。
 *
 * 锁定语义：每个转换是**投影而非改写**（同引用透传）+ 便宜的 devOnly
 * 形状守卫（表达式 = 数组 + 字符串头；守卫失败仍 cast，只在开发期
 * 告警 —— 完整校验仍由 MapLibre addLayer 诚实失败）。
 */

describe("asExpression：裸 JSON 数组 → ExpressionSpecification", () => {
  beforeEach(() => {
    // 守卫走 process.env.NODE_ENV 运行时读取；逐用例钉住环境防漂移。
    vi.stubEnv("NODE_ENV", "development");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("合法表达式（数组 + 字符串头）→ 同引用透传且 dev 下不告警", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const expr = ["get", "x"];
    const out = asExpression(expr);
    expect(out).toBe(expr);
    expect(warn).not.toHaveBeenCalled();
  });

  it("dev 下非表达式形状（标量）→ 仍 cast 返回原值，但产出 devOnly 告警", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const out = asExpression("red");
    expect(out).toBe("red");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]?.[0]).toContain("asExpression");
  });

  it("数组但非字符串头（如 line-dasharray 数值数组）→ dev 下告警但不改写", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const dash = [4, 2];
    const out = asExpression(dash);
    expect(out).toBe(dash);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("production 模式 → 即使非表达式形状也不告警（告警是 devOnly）", () => {
    vi.stubEnv("NODE_ENV", "production");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const out = asExpression("red");
    expect(out).toBe("red");
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("asPaintValue：开放 JSON paint 值 → MapLibrePaintValue", () => {
  it("数组 → 走 asExpression 边界（同引用）", () => {
    const expr = ["interpolate", ["linear"], ["zoom"], 0, "#fff", 12, "#000"];
    expect(asPaintValue(expr)).toBe(expr);
  });

  it("标量直通：string / number / boolean / null", () => {
    expect(asPaintValue("red")).toBe("red");
    expect(asPaintValue(0.8)).toBe(0.8);
    expect(asPaintValue(true)).toBe(true);
    expect(asPaintValue(null)).toBeNull();
  });

  it("StyleMethod dict（语义对象）→ 显式透传、同引用（MapLibre 校验兜底）", () => {
    const method = { method: "interpolate", field: "weight", stops: [[1, 1], [120, 8]] };
    expect(asPaintValue(method)).toBe(method);
  });
});

describe("asLayerSpecification：CompiledLayer → 官方 LayerSpecification", () => {
  it("id/type 保真且同一运行时对象（投影非拷贝）", () => {
    const compiled = {
      id: "poi-circle",
      type: "circle" as const,
      layout: {},
      paint: { "circle-radius": 5 },
    };
    const out = asLayerSpecification(compiled);
    expect(out).toBe(compiled);
    expect(out.id).toBe("poi-circle");
    expect(out.type).toBe("circle");
  });

  it("携带 source / minzoom / filter 的完整图层 → 字段保真", () => {
    const compiled = {
      id: "roads-line",
      type: "line" as const,
      source: "osm",
      "source-layer": "roads",
      layout: {},
      paint: {},
      minzoom: 8,
      maxzoom: 20,
      filter: ["==", "$type", "LineString"],
    };
    const out = asLayerSpecification(compiled);
    expect(out.id).toBe("roads-line");
    expect(out.minzoom).toBe(8);
    expect(out.maxzoom).toBe(20);
  });
});

describe("asCompiledStyleView：编译结果视图引用保真", () => {
  it("layers / sources 与原 style 同引用（视图 = 同一对象的诚实投影）", () => {
    const circleLayer = asLayerSpecification({
      id: "l-circle",
      type: "circle",
      layout: {},
      paint: {},
    });
    const lineLayer = asLayerSpecification({
      id: "l-line",
      type: "line",
      layout: {},
      paint: {},
    });
    // 注解成官方 SourceSpecification，让字面量获得上下文类型（type: "geojson"）
    const sources: Record<string, SourceSpecification> = {
      base: { type: "geojson", data: "https://example.com/base.geojson" },
    };
    const style: CompiledStyle = {
      version: 8,
      name: "test-style",
      center: [104.06, 30.57],
      zoom: 10,
      sources,
      layers: [circleLayer, lineLayer],
    };

    const view = asCompiledStyleView(style);
    expect(view.version).toBe(8);
    expect(view.layers).toBe(style.layers);
    expect(view.layers[0]).toBe(circleLayer);
    expect(view.layers[1]?.id).toBe("l-line");
    expect(view.sources).toBe(sources);
    expect(view.sources.base).toBe(sources.base);
    expect(view.name).toBe("test-style");
  });
});
