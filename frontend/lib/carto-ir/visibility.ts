/**
 * 可见性/zoom 单一 precedence（C11）—— 渲染端唯一裁决器。
 *
 * 背景（F10 Out of Scope 的收口）：可见性与 zoom 语义此前散落四处 ——
 * MapSpec `layer.visible`（后端 authored，前端无消费）、MapLibre 原生
 * `layout.visibility`（用户 durable 决策通道）、v1.5 起的
 * `layer.visibility` 结构 zoom 门、grammar 尺度带的 visibility_hints
 * （advisory，前端零消费）。本模块把它们收敛成**一个纯函数裁决面**，
 * live 路径（MapSpecRuntime.addLayerSafe）与 headless 编译路径
 * （compileMapSpec）共用 —— 双路径不再漂移。
 *
 * ## 显隐维度（同一时刻该层是否显示）
 *
 * 1. `layout.visibility`（用户 durable 决策通道，visibility-transaction/
 *    user-mutation 写入）—— 恒最优先，存在即终裁；
 * 2. `layer.visible === false`（authored 意图）—— 仅当 (1) 缺失时作为
 *    layout.visibility 初值；
 * 3. 其余 = "visible"。
 *
 * ## zoom 维度（该层在什么 zoom 窗口存在）
 *
 * 与显隐维度**正交**（MapLibre minzoom/maxzoom 是层级窗口，不随用户
 * 显隐切换变化）：
 * 1. `visibility.min_zoom`/`max_zoom`（结构门，后端 authored 显式值）；
 * 2. `visibility.hints` 的 opt-in 词表键（当前仅 `street_detail_minzoom`
 *    是真可见性阈值 —— "street 级细节自 zoom v 起出现"）作为下限参与
 *    max 合成；
 * 3. 其余词表键（`admin_boundary_detail`）是泛化档标记（与
 *    scale_rules.boundary_detail 同源），**不是**可见性阈值 —— 参与裁决
 *    会把参考语境边界层在小 zoom 整层藏掉（语义反转），故只随披露面
 *    透出，不进门控；
 * 4. 未知词表键：不拒绝（extra="allow" 纪律）、进门控披露。
 *
 * zoom 值域 [0, 24]（MapLibre 契约）；min ≥ max 的病态门按披露 + 不设门
 * 处理（渲染器无从表达空窗口 —— 诚实失败优于静默黑图）。
 */

import type { MapSpecLayer, MapSpecLayerVisibility } from "@/lib/mapspec-compiler/types";

/** 可见性提示词表（= 后端 mapspec_schema.VISIBILITY_HINT_KEYS）。 */
export const VISIBILITY_HINT_KEYS = [
  "street_detail_minzoom",
  "admin_boundary_detail",
] as const;

export type VisibilityHintKey = (typeof VISIBILITY_HINT_KEYS)[number];

/** 参与 zoom 门控的提示键（语义 = 该层自该 zoom 起才应有细节）。 */
const GATING_HINT_KEYS: readonly VisibilityHintKey[] = ["street_detail_minzoom"];

export const MAPLIBRE_ZOOM_MIN = 0;
export const MAPLIBRE_ZOOM_MAX = 24;

/** 显隐裁决结果（MapLibre layout.visibility 词表）。 */
export type ResolvedVisibility = "visible" | "none";

/** zoom 门（MapLibre 层级 minzoom/maxzoom；undefined = 不设门）。 */
export interface ZoomGate {
  minzoom?: number;
  maxzoom?: number;
}

export interface VisibilityResolution {
  visibility: ResolvedVisibility;
  /** 显隐是否由 authored `visible:false` 推出（= layout.visibility 缺失）。 */
  fromAuthoredVisible: boolean;
  /** layout.visibility 是词表外值（真值字符串等）—— 裁决为 visible 并
   *  披露（S3 review P2-4b：旧行为 MapLibre addLayer 响亮失败，静默折算
   *  必须留痕）。 */
  invalidLayoutVisibility?: unknown;
  gate: ZoomGate;
  /** 门控中被忽略的提示键（含非门控词表键与未知键）—— 结构化披露。 */
  ignoredHints: Array<{
    key: string;
    value: number;
    reason: "non_gating_hint" | "unknown_hint" | "degenerate_gate" | "non_numeric_hint";
  }>;
}

function clampZoom(v: number): number | undefined {
  if (!Number.isFinite(v)) return undefined;
  return Math.min(MAPLIBRE_ZOOM_MAX, Math.max(MAPLIBRE_ZOOM_MIN, v));
}

/**
 * 显隐维度裁决（纯函数；见模块头 precedence 1–3）。
 */
export function resolveVisibility(layer: Pick<MapSpecLayer, "visible" | "layout">): {
  visibility: ResolvedVisibility;
  fromAuthoredVisible: boolean;
  invalidLayoutVisibility?: unknown;
} {
  const explicit = layer.layout?.visibility;
  if (explicit === "visible" || explicit === "none") {
    return { visibility: explicit, fromAuthoredVisible: false };
  }
  if (explicit !== undefined && explicit !== null) {
    // 词表外真值：裁决 visible（保守默认），披露原值。
    const fallback = layer.visible === false
      ? { visibility: "none" as const, fromAuthoredVisible: true }
      : { visibility: "visible" as const, fromAuthoredVisible: false };
    return { ...fallback, invalidLayoutVisibility: explicit };
  }
  if (layer.visible === false) {
    return { visibility: "none", fromAuthoredVisible: true };
  }
  return { visibility: "visible", fromAuthoredVisible: false };
}

/**
 * zoom 维度裁决（纯函数；见模块头 precedence 1–4）。
 * min ≥ max 的病态门 → 空门 + 披露（渲染器无从表达空窗口）。
 */
export function resolveZoomGate(
  layer: Pick<MapSpecLayer, "visibility">,
): { gate: ZoomGate; ignoredHints: VisibilityResolution["ignoredHints"] } {
  const vis: MapSpecLayerVisibility | undefined = layer.visibility;
  const ignoredHints: VisibilityResolution["ignoredHints"] = [];
  if (!vis) return { gate: {}, ignoredHints };

  const floors: number[] = [];
  const explicitMin = clampZoom(typeof vis.min_zoom === "number" ? vis.min_zoom : NaN);
  const explicitMax = clampZoom(typeof vis.max_zoom === "number" ? vis.max_zoom : NaN);

  if (vis.hints && typeof vis.hints === "object") {
    for (const [key, value] of Object.entries(vis.hints)) {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        // S3 review P3-4：非数值 hint 不静默跳过 —— 进披露面。
        ignoredHints.push({ key, value: NaN, reason: "non_numeric_hint" });
        continue;
      }
      if ((GATING_HINT_KEYS as readonly string[]).includes(key)) {
        const clamped = clampZoom(value);
        if (clamped !== undefined) floors.push(clamped);
      } else {
        ignoredHints.push({
          key,
          value,
          reason: (VISIBILITY_HINT_KEYS as readonly string[]).includes(key)
            ? "non_gating_hint"
            : "unknown_hint",
        });
      }
    }
  }

  const min = floors.length > 0 ? Math.max(...floors, explicitMin ?? -Infinity) : explicitMin;
  let minzoom = min === undefined ? undefined : min;
  let maxzoom = explicitMax;
  if (
    minzoom !== undefined &&
    maxzoom !== undefined &&
    minzoom >= maxzoom
  ) {
    ignoredHints.push({
      key: "visibility.min_zoom>=max_zoom",
      value: minzoom,
      reason: "degenerate_gate",
    });
    minzoom = undefined;
    maxzoom = undefined;
  }
  return { gate: { minzoom, maxzoom }, ignoredHints };
}

/**
 * 单一裁决入口：显隐 + zoom 门一次算齐（两条渲染路径共用）。
 */
export function resolveLayerVisibility(
  layer: Pick<MapSpecLayer, "visible" | "layout" | "visibility">,
): VisibilityResolution {
  const { visibility, fromAuthoredVisible, invalidLayoutVisibility } = resolveVisibility(layer);
  const { gate, ignoredHints } = resolveZoomGate(layer);
  return { visibility, fromAuthoredVisible, invalidLayoutVisibility, gate, ignoredHints };
}
