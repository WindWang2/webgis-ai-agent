/**
 * bivariate 原生 MapModel 的渲染端校验与降级（C11 v1.5）。
 *
 * 后端 converter 自 v1.5 起在 layer.bivariate 声明语义身份
 * （x_field/y_field/matrix/class_field）；色值权威仍在 paint 的 match
 * 表达式（class_field → 3×3 类别索引）。本模块回答渲染器的两个问题：
 *
 * 1. **一致性**：paint 是否真的按 class_field match（声明 ↔ 表达漂移
 *    检测 —— 此前只能靠 legend_spec.class_field 字符串约定）；
 * 2. **降级**：声明不完整/矩阵规模不支持/paint 缺失时，回退什么 ——
 *    fill 层回退常量色 + 披露，绝不白图（v4 既有语义的 typed 收口）。
 *
 * 纯函数、无 IO。
 */

import type { MapSpecLayer, MapSpecLayerBivariate } from "@/lib/mapspec-compiler/types";
import { CONTRACT_BIVARIATE_MATRIX_SIZES as BIVARIATE_MATRIX_SIZES } from "./contract-manifest";

export type BivariateCheck =
  | {
      status: "ok";
      spec: MapSpecLayerBivariate;
      /** paint 中匹配到的 class_field 引用数（诊断面）。 */
      paintClassFieldRefs: number;
    }
  | {
      status: "degraded";
      spec: MapSpecLayerBivariate | null;
      reason:
        | "matrix_unsupported"
        | "class_field_missing"
        | "paint_not_match"
        | "field_contract_incomplete";
      detail: string;
      /** 降级后仍可用的常量色（来自 paint.color / paint["fill-color"]）。 */
      fallbackColor: string | null;
    };

const DEFAULT_FALLBACK_COLOR = "#e5e7eb";

/**
 * 校验 bivariate 声明并给出降级决策。
 * `layer.bivariate` 缺失 → 非 bivariate 层（不产出 check —— 调用方
 * 以 `layer.bivariate != null` 为准进入本函数）。
 */
export function checkBivariateLayer(layer: MapSpecLayer): BivariateCheck | null {
  const raw = layer.bivariate;
  if (!raw) return null;

  const fallbackColor = pickFallbackColor(layer);

  if (
    typeof raw !== "object" ||
    typeof (raw as MapSpecLayerBivariate).x_field !== "string" ||
    typeof (raw as MapSpecLayerBivariate).y_field !== "string" ||
    !(raw as MapSpecLayerBivariate).x_field ||
    !(raw as MapSpecLayerBivariate).y_field ||
    (raw as MapSpecLayerBivariate).x_field === (raw as MapSpecLayerBivariate).y_field
  ) {
    return {
      status: "degraded",
      spec: null,
      reason: "field_contract_incomplete",
      detail: "bivariate.x_field/y_field missing, non-string, or identical; falling back to constant color",
      fallbackColor,
    };
  }
  const spec = raw as MapSpecLayerBivariate;

  if (!(BIVARIATE_MATRIX_SIZES as readonly number[]).includes(spec.matrix ?? 3)) {
    return {
      status: "degraded",
      spec,
      reason: "matrix_unsupported",
      detail: `bivariate.matrix ${String(spec.matrix)} unsupported (supported: ${BIVARIATE_MATRIX_SIZES.join("/")}); falling back to constant color`,
      fallbackColor,
    };
  }

  const classField = spec.class_field || "__biv_class";
  const paintRefs = countPaintClassFieldRefs(layer, classField);
  if (paintRefs === 0) {
    return {
      status: "degraded",
      spec,
      reason: "paint_not_match",
      detail: `paint carries no match expression on ${classField}; falling back to constant color`,
      fallbackColor,
    };
  }

  return { status: "ok", spec, paintClassFieldRefs: paintRefs };
}

/** paint 值树里 `["get", classField]` 引用计数（cheap 深度优先）。 */
function countPaintClassFieldRefs(layer: MapSpecLayer, classField: string): number {
  let count = 0;
  const visit = (node: unknown, depth: number): void => {
    if (depth > 12 || node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      if (node[0] === "get" && node[1] === classField) count++;
      for (const child of node) visit(child, depth + 1);
      return;
    }
    for (const child of Object.values(node as Record<string, unknown>)) {
      visit(child, depth + 1);
    }
  };
  visit(layer.paint, 0);
  return count;
}

/**
 * 降级常量色：优先 paint 里已有的首色，否则中性灰（不虚构主题色）。
 * 两套方言都查：原生键（adapter 面）与语义短键 `color`（converter 面
 * —— spec_to_paint 产出）。
 */
function pickFallbackColor(layer: MapSpecLayer): string | null {
  const paint = (layer.paint ?? {}) as Record<string, unknown>;
  for (const key of ["fill-color", "circle-color", "line-color", "fill-extrusion-color", "color"]) {
    const v = paint[key];
    if (typeof v === "string" && v) return v;
  }
  return DEFAULT_FALLBACK_COLOR;
}
