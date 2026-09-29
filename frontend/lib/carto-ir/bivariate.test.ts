import { describe, it, expect } from "vitest";
import { checkBivariateLayer } from "./bivariate";
import type { MapSpecLayer, MapSpecLayerBivariate, MapSpecLayerPaint } from "@/lib/mapspec-compiler/types";

/**
 * C11 v1.5 bivariate 渲染端校验与降级测试。
 *
 * 锁定决策面：
 *  - 声明 ↔ 表达一致性：paint 必须真的按 class_field（缺省 __biv_class）
 *    match（["get", classField] 引用 ≥ 1）；
 *  - 合同完整性：x_field/y_field 缺失/同值 → field_contract_incomplete
 *    （spec 置 null —— 半份合同不是合同）；
 *  - 矩阵规模词表 [2,3]（= 后端 BIVARIATE_MATRIX_SIZES）；缺省按 3；
 *  - 降级绝不白图：fallbackColor 取 paint 首个色键字符串，全无色 →
 *    中性灰 "#e5e7eb"。
 */

/** 构造最小 fill 图层。 */
function layerWith(overrides: Partial<MapSpecLayer>): MapSpecLayer {
  return { id: "biv-fill", source: "biv-src", type: "fill", ...overrides };
}

/** MapSpec paint 是开放 dict；裸 JSON 数组（MapLibre 表达式形态）经 typed 收窄进入。 */
function paintOf(raw: Record<string, unknown>): MapSpecLayerPaint {
  return raw as unknown as MapSpecLayerPaint;
}

describe("checkBivariateLayer：非 bivariate 层", () => {
  it("无 bivariate 声明 → null（调用方以 layer.bivariate != null 为准）", () => {
    expect(checkBivariateLayer(layerWith({ paint: paintOf({ "fill-color": "#fff" }) }))).toBeNull();
  });
});

describe("checkBivariateLayer：ok 分支", () => {
  it("paint 按 class_field match 且 x≠y、matrix 缺省(按 3) → ok", () => {
    const layer = layerWith({
      paint: paintOf({
        "fill-color": [
          "match",
          ["get", "__biv_class"],
          "0", "#e5f5f9",
          "1", "#99d8c9",
          "2", "#2ca25f",
          "#66c2a4",
        ],
      }),
      bivariate: { x_field: "pop_density", y_field: "green_cover" },
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("ok");
    if (result?.status === "ok") {
      expect(result.paintClassFieldRefs).toBe(1);
      expect(result.spec.x_field).toBe("pop_density");
      expect(result.spec.y_field).toBe("green_cover");
    }
  });

  it("显式 matrix 2（词表内）→ ok", () => {
    const layer = layerWith({
      paint: paintOf({
        "fill-color": ["match", ["get", "__biv_class"], "0", "#aaa", "#bbb"],
      }),
      bivariate: { x_field: "a", y_field: "b", matrix: 2 },
    });
    expect(checkBivariateLayer(layer)?.status).toBe("ok");
  });

  it("paint 树内嵌套位置的 get 引用也被计数（case 内 + 独立色键）", () => {
    const layer = layerWith({
      paint: paintOf({
        "fill-color": [
          "case",
          ["==", ["get", "__biv_class"], "0"],
          "#aaa",
          "#bbb",
        ],
        "fill-outline-color": ["get", "__biv_class"],
      }),
      bivariate: { x_field: "a", y_field: "b" },
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("ok");
    if (result?.status === "ok") expect(result.paintClassFieldRefs).toBe(2);
  });
});

describe("checkBivariateLayer：degraded 分支", () => {
  it("x_field === y_field → field_contract_incomplete、spec 置 null", () => {
    const layer = layerWith({
      paint: paintOf({ "fill-color": "#0ea5e9" }),
      bivariate: { x_field: "same", y_field: "same" },
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") {
      expect(result.reason).toBe("field_contract_incomplete");
      expect(result.spec).toBeNull();
      expect(result.fallbackColor).toBe("#0ea5e9");
    }
  });

  it("matrix 越界（5）→ matrix_unsupported，spec 原样返回", () => {
    const layer = layerWith({
      paint: paintOf({ "fill-color": "#123456" }),
      // matrix 词表外值在生产数据里可能来自旧后端 —— 经 typed 收窄注入
      bivariate: { x_field: "a", y_field: "b", matrix: 5 } as unknown as MapSpecLayerBivariate,
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") {
      expect(result.reason).toBe("matrix_unsupported");
      expect(result.spec?.matrix).toBe(5);
      expect(result.fallbackColor).toBe("#123456");
      expect(result.detail).toContain("unsupported");
    }
  });

  it("paint 无 match 表达式 → paint_not_match，fallbackColor 取 paint 首个色键字符串", () => {
    const layer = layerWith({
      paint: paintOf({ "fill-color": "#f97316" }),
      bivariate: { x_field: "a", y_field: "b" },
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") {
      expect(result.reason).toBe("paint_not_match");
      expect(result.spec).not.toBeNull();
      expect(result.detail).toContain("__biv_class");
      expect(result.fallbackColor).toBe("#f97316");
    }
  });

  it("paint 全无色键字符串 → 中性灰 #e5e7eb（不虚构主题色、绝不白图）", () => {
    const layer = layerWith({
      paint: paintOf({ "fill-opacity": 0.8 }),
      bivariate: { x_field: "a", y_field: "b" },
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") {
      expect(result.reason).toBe("paint_not_match");
      expect(result.fallbackColor).toBe("#e5e7eb");
    }
  });

  it("无 paint 结构 → paint_not_match + 缺省灰", () => {
    const layer = layerWith({ bivariate: { x_field: "a", y_field: "b" } });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") expect(result.fallbackColor).toBe("#e5e7eb");
  });

  it("class_field 显式覆盖缺省 __biv_class → 按声明字段核对 paint", () => {
    const layer = layerWith({
      paint: paintOf({
        "fill-color": ["match", ["get", "__biv_class"], "0", "#aaa", "#bbb"],
      }),
      bivariate: { x_field: "a", y_field: "b", class_field: "biv_cls_v2" },
    });
    const result = checkBivariateLayer(layer);
    // paint 引用的是缺省字段而声明改为 biv_cls_v2 → 一致性失败，诚实降级
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") expect(result.reason).toBe("paint_not_match");
  });

  it("bivariate 非对象（字符串）→ field_contract_incomplete", () => {
    const layer = layerWith({
      paint: paintOf({}),
      bivariate: "bogus" as unknown as MapSpecLayerBivariate,
    });
    const result = checkBivariateLayer(layer);
    expect(result?.status).toBe("degraded");
    if (result?.status === "degraded") expect(result.reason).toBe("field_contract_incomplete");
  });
});
