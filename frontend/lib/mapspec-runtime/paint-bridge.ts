import { compileStyleMethod, isStyleMethodObject } from "@/lib/mapspec-compiler/compiler";
import type { MapSpecLayer, StyleMethod } from "@/lib/mapspec-compiler/types";

/**
 * MapSpec paint 方言桥(live runtime → MapLibre 边界)。
 *
 * 两个合法生产者、两套 paint 键方言会在 addLayerSafe 汇合:
 *  - 后端 MapSpec(analysis_cartography_converter / spec_to_paint)发规范短键
 *    `color`/`radius`/`width`/`opacity`/`strokeColor`…,值可能是 StyleMethod
 *    对象(热力图除外——converter 的 heatmap 分支发 MapLibre 原生表达式);
 *  - 前端 adapter(hudStateToMapSpec)发 MapLibre 原生键(`fill-color` 等),
 *    值已是原生表达式,必须原样直通。
 *
 * 此前 runtime 把 paint 原样传给 `map.addLayer`,后端方言以
 * `layers.<id>.paint.color: unknown property "color"` 被 MapLibre 拒绝。
 * 本桥对齐 headless 编译器(compiler.ts)的语义:规范键按图层类型降级为
 * 原生键(StyleMethod 值经 compileStyleMethod 降低,raw 值直通,优先级高于
 * 同目标的原生键);已原生键按类型前缀直通;其余键(MapLibre 必然不认识)
 * 丢弃而非放行报错。
 */

const CANONICAL_PAINT_KEYS: Record<string, Record<string, string>> = {
  circle: {
    color: "circle-color",
    radius: "circle-radius",
    opacity: "circle-opacity",
    strokeColor: "circle-stroke-color",
    strokeWidth: "circle-stroke-width",
  },
  line: {
    color: "line-color",
    width: "line-width",
    opacity: "line-opacity",
  },
  fill: {
    color: "fill-color",
    opacity: "fill-opacity",
    strokeColor: "fill-outline-color",
  },
  "fill-extrusion": {
    color: "fill-extrusion-color",
    opacity: "fill-extrusion-opacity",
    height: "fill-extrusion-height",
    base: "fill-extrusion-base",
  },
  heatmap: {
    radius: "heatmap-radius",
    opacity: "heatmap-opacity",
    intensity: "heatmap-intensity",
    weight: "heatmap-weight",
  },
  raster: {
    opacity: "raster-opacity",
  },
};

const FALLBACK_COLOR = "#cccccc";

/**
 * MapLibre rejects color expressions with the wrong arity:
 *   case         — length including op must be even (≥4):  ["case", cond, out, fallback]
 *                  odd length → "Expected an odd number of arguments."
 *   match        — length must be odd (≥5)
 *   interpolate  — length must be odd (≥7, at least two stops)
 *   step         — length must be odd (≥3)
 * Agent-authored MapSpec (Pi layer_upsert) often omits the match/case fallback.
 */
export function sanitizeMapLibreExpression(
  value: unknown,
  fallback: string = FALLBACK_COLOR,
): unknown {
  if (!Array.isArray(value) || typeof value[0] !== "string") return value;
  const op = value[0];
  if (op === "case") {
    // MapLibre case.ts: args.length must be even and ≥4.
    if (value.length < 3) return fallback;
    if (value.length % 2 !== 0) return [...value, fallback];
    return value;
  }
  if (op === "match") {
    // MapLibre match.ts: args.length must be odd and ≥5.
    if (value.length < 4) return fallback;
    if (value.length % 2 === 0) return [...value, fallback];
    return value;
  }
  if (op === "interpolate" || op === "step") {
    if (value.length % 2 === 0) return [...value, fallback];
    return value;
  }
  return value;
}

/** heatmap 的规范 `color`(raw hex)→ 透明→热色密度 ramp,与 compiler.ts 热力图分支同形。 */
function heatmapColorRamp(hotColor: string): unknown[] {
  return [
    "interpolate",
    ["linear"],
    ["heatmap-density"],
    0,
    "rgba(0,0,0,0)",
    0.1,
    hotColor,
    1,
    hotColor,
  ];
}

/** 键是否是该图层类型的合法 MapLibre 原生 paint 键(按命名空间前缀判定)。 */
function isNativePaintKey(layerType: string, key: string): boolean {
  if (key.startsWith(`${layerType}-`)) return true;
  // symbol 的 paint 命名空间是 icon-*/text-*,没有 symbol-* 前缀。
  if (layerType === "symbol") {
    return key.startsWith("icon-") || key.startsWith("text-");
  }
  return false;
}

/**
 * 把 MapSpecLayer 的 paint 规范成 MapLibre 可接受的 paint dict。纯函数:
 * 不触 MapLibre、不改入参;缺失/空 paint 返回 `{}`。
 */
export function toMapLibrePaint(layer: MapSpecLayer): Record<string, unknown> {
  const source = layer.paint;
  if (!source || typeof source !== "object") return {};

  const canonical = CANONICAL_PAINT_KEYS[layer.type] || {};
  const out: Record<string, unknown> = {};

  // 规范键先行降级 —— 与编译器优先级一致:规范键压过同目标的原生键。
  for (const [key, nativeKey] of Object.entries(canonical)) {
    const value = (source as Record<string, unknown>)[key] as StyleMethod | undefined;
    if (value !== undefined) {
      out[nativeKey] = compileStyleMethod(value);
    }
  }

  // heatmap 的 `color` 语义特殊:要素属性表达式表达不了 heatmap-density,
  // 契约约定 raw hex 字符串 → 密度 ramp(compiler.ts 同款)。
  const heatmapColor = (source as Record<string, unknown>).color;
  if (layer.type === "heatmap" && typeof heatmapColor === "string") {
    out["heatmap-color"] = heatmapColorRamp(heatmapColor);
  }

  // 其余键:仅原生键直通,且不覆盖规范键已产出的目标;无法映射的键丢弃。
  for (const [key, value] of Object.entries(source)) {
    if (key in canonical) continue;
    if (layer.type === "heatmap" && key === "color") continue;
    if (!isNativePaintKey(layer.type, key)) continue;
    if (out[key] === undefined) {
      out[key] = isStyleMethodObject(value) ? compileStyleMethod(value as StyleMethod) : value;
    }
  }

  for (const [key, value] of Object.entries(out)) {
    out[key] = sanitizeMapLibreExpression(value);
  }

  return out;
}
