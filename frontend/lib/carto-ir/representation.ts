/**
 * 语义缩放表达窗口单一裁决器（v1.6，方向 5）—— live runtime 与
 * headless 编译器共用的纯函数面。
 *
 * 权威链：后端 ``app/lib/cartography/representation.py`` 产生窗口计划 →
 * MapSpec ``layer.representation.windows``（desired state 声明）→ 本模块
 * 归一化/查询 → compiler/runtime 把窗口编译为 MapLibre 原生
 * minzoom/maxzoom 门（零 per-frame JS 切换；zoom 手势不回写 spec）。
 *
 * 语义（与后端 normalize_windows 逐条对齐，golden corpus 锁 parity）：
 * - mode ∈ CONTRACT_REPRESENTATION_MODES；钳制 [0,24] 后 min<max
 *   （整体越界窗钳成零宽 → 拒，同后端 #1625 / #1668）；
 * - 排序后必须连续覆盖 [0,24)（缺口计划无从消费 → null + 证据披露，
 *   消费方回退层缺省，绝不猜）；
 * - 相邻同模式窗合并；窗口数 ≤ 8；
 * - ``hidden`` 窗口 = detail visibility 门（渲染端不产出层；必带
 *   reasonCodes —— 后端生产契约）。
 *
 * user-wins：用户显隐（layout.visibility 通道）与窗口正交且恒优先；
 * 用户 pin 在 authoring 期已由后端落为单一窗口（本模块不二次猜测）。
 */

import {
  CONTRACT_REPRESENTATION_MODES,
} from "./contract-manifest";
import type { MapSpecLayer, MapSpecRepresentationWindow } from "@/lib/mapspec-compiler/types";

export const REPRESENTATION_MODES = CONTRACT_REPRESENTATION_MODES;
export type RepresentationMode = (typeof REPRESENTATION_MODES)[number];

/** 渲染端可自行执行的模式（无需后端工件）。 */
export const RENDERER_EXECUTABLE_MODES: readonly RepresentationMode[] = [
  "raw",
  "point_cluster",
  "visual_heatmap",
];

/** artifact-backed 模式（声明即「后端确认工件可用」；前端透传渲染）。 */
export const ARTIFACT_MODES: readonly RepresentationMode[] = ["aggregate_grid", "generalized"];

export const MAPLIBRE_ZOOM_MIN = 0;
export const MAPLIBRE_ZOOM_MAX = 24;

/** 与后端 MAX_WINDOWS 同值（manifest 漂移闸兜底）。 */
export const MAX_WINDOWS = 8;

const MODES: readonly string[] = REPRESENTATION_MODES;
const CONTIGUITY_EPSILON = 1e-9;

export interface NormalizedRepresentationWindow {
  minZoom: number;
  maxZoom: number;
  mode: RepresentationMode;
  reasonCodes: string[];
}

export interface RepresentationPlan {
  windows: NormalizedRepresentationWindow[];
  /** 非平凡（存在非 raw 窗口）—— 平凡计划的层走既有缺省表达。 */
  nonTrivial: boolean;
  /** cluster 窗口在场（源级聚合 + 簇子层需要）。 */
  hasCluster: boolean;
  /** hidden 窗口在场（detail visibility 门）。 */
  hasHidden: boolean;
}

interface RawWindow {
  min: number;
  max: number;
  mode: string;
  reasonCodes: string[];
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * spec 层 → 规范化表达计划。无声明/平凡全 raw/非法 → null（消费方
 * 回退既有表达 + evidence 披露，绝不静默猜模式）。
 */
export function resolveRepresentationPlan(
  layer: Pick<MapSpecLayer, "representation">,
): RepresentationPlan | null {
  const raw = layer.representation?.windows;
  if (!Array.isArray(raw) || raw.length === 0) return null;

  const rows: RawWindow[] = [];
  for (const w of raw as MapSpecRepresentationWindow[]) {
    if (!w || typeof w !== "object") return null;
    const mode = (w as { mode?: unknown }).mode;
    if (typeof mode !== "string" || !MODES.includes(mode)) return null;
    const mn = (w as { min_zoom?: unknown }).min_zoom;
    const mx = (w as { max_zoom?: unknown }).max_zoom;
    if (!isFiniteNumber(mn) || !isFiniteNumber(mx)) return null;
    const rawCodes = (w as { reason_codes?: unknown }).reason_codes;
    const reasonCodes = Array.isArray(rawCodes)
      ? rawCodes.filter((c): c is string => typeof c === "string").slice(0, 8)
      : [];
    // #1668（后端 #1625 同款 parity）：钳制到 [0,24] 后复核 min<max（文档契约
    // 「钳制后 min<max」）—— 整体越界窗（两端都 <0 或都 >24，如 [-10,-5] /
    // [25,30]）钳制后退化为零宽 [0,0]/[24,24]，与 [8,8] 同为空窗，必须拒；
    // 部分越界仍合法（[-5,8]→[0,8]、[20,30]→[20,24]）。钳制前校验会放过
    // 整体越界窗并产出退化窗，与后端行为漂移。
    const min = Math.min(MAPLIBRE_ZOOM_MAX, Math.max(MAPLIBRE_ZOOM_MIN, mn));
    const max = Math.min(MAPLIBRE_ZOOM_MAX, Math.max(MAPLIBRE_ZOOM_MIN, mx));
    if (!(min < max)) return null;
    rows.push({
      min,
      max,
      mode,
      reasonCodes,
    });
  }
  rows.sort((a, b) => a.min - b.min);
  if (rows[0].min > MAPLIBRE_ZOOM_MIN || rows[rows.length - 1].max < MAPLIBRE_ZOOM_MAX) {
    return null; // 未覆盖全域（缺口计划无从消费）
  }
  const merged: RawWindow[] = [];
  let cursor = MAPLIBRE_ZOOM_MIN;
  for (const w of rows) {
    if (Math.abs(w.min - cursor) > CONTIGUITY_EPSILON) return null; // 缺口
    cursor = w.max;
    const prev = merged[merged.length - 1];
    if (prev && prev.mode === w.mode) {
      prev.max = w.max;
      for (const c of w.reasonCodes) {
        if (!prev.reasonCodes.includes(c) && prev.reasonCodes.length < 8) {
          prev.reasonCodes.push(c);
        }
      }
    } else {
      merged.push({ ...w, reasonCodes: [...w.reasonCodes] });
    }
  }
  if (merged.length === 0 || merged.length > MAX_WINDOWS) return null;

  const windows: NormalizedRepresentationWindow[] = merged.map((w) => ({
    minZoom: w.min,
    maxZoom: w.max,
    mode: w.mode as RepresentationMode,
    reasonCodes: w.reasonCodes,
  }));
  return {
    windows,
    nonTrivial: windows.some((w) => w.mode !== "raw"),
    hasCluster: windows.some((w) => w.mode === "point_cluster"),
    hasHidden: windows.some((w) => w.mode === "hidden"),
  };
}

/**
 * 计划 + zoom → 活跃模式（半开区间 [min,max)；越界钳到首/末窗）。
 * 供导出（C6：固定 zoom 一次裁决）与 QA 对账消费。
 */
export function representationModeAtZoom(
  plan: RepresentationPlan,
  zoom: number,
): RepresentationMode {
  const z = Math.min(MAPLIBRE_ZOOM_MAX - CONTIGUITY_EPSILON, Math.max(MAPLIBRE_ZOOM_MIN, zoom));
  for (const w of plan.windows) {
    if (w.minZoom <= z && z < w.maxZoom) return w.mode;
  }
  return plan.windows[plan.windows.length - 1].mode;
}

/**
 * 活跃 raw 窗口集合（主层门=首个 raw 窗；其余 raw 窗由 rep 子层承载）。
 * 返回按序的全部 raw 窗（首元素 = 主层）。
 */
export function rawWindowsOf(plan: RepresentationPlan): NormalizedRepresentationWindow[] {
  return plan.windows.filter((w) => w.mode === "raw");
}

/**
 * 非主层窗口（主层 = 首个 raw 窗；无 raw 窗时主层承载首窗语义由
 * 调用方按层型决定）。每窗产出一条展开指令（mode + 门）。
 */
export function expansionWindowsOf(
  plan: RepresentationPlan,
): Array<{ index: number } & NormalizedRepresentationWindow> {
  const firstRawIdx = plan.windows.findIndex((w) => w.mode === "raw");
  return plan.windows
    .map((w, index) => ({ index, ...w }))
    .filter((w) => w.index !== firstRawIdx);
}

/** 子层 id 命名（`${layerId}__rep${i}` —— `__` 前缀进用户显隐级联）。 */
export function representationSublayerId(layerId: string, windowIndex: number): string {
  return `${layerId}__rep${windowIndex}`;
}
