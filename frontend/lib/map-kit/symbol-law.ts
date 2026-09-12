/**
 * Adaptive Symbol Law (AC-06, ADR-0155) — the single source of truth that
 * turns zoom / feature density / geometry type into MapLibre paint
 * expressions, replacing the hardcoded symbology constants
 * (fill-opacity 0.8, circle-radius 6, heatmap radius 30px …).
 *
 * Design constraints:
 *  - Pure and deterministic: same inputs → structurally identical expressions.
 *    No MapLibre import (testable headless; safe for the worker-adjacent
 *    compiler path).
 *  - Count dependence is resolved in JS (baked into interpolation stops);
 *    zoom dependence stays inside the expression so MapLibre interpolates
 *    smoothly between stops while panning/zooming.
 *  - `densitySignal` is THE shared density signal: the 05 line (labels) is
 *    contractually required to import it from here instead of growing a
 *    second copy (§8 协调点; §0.5 冲突契约).
 *  - Bounded evidence ring: density switches, law applications and unmapped
 *    keys land in `recordSymbolLawEvidence` — nothing is dropped silently.
 */

export type SymbolGeometryType = "Point" | "LineString" | "Polygon";

export type DensityLevel = "sparse" | "normal" | "dense" | "very-dense";

/** 密度切换阈值（§0.5：以 VIEWPORT_RENDER_BUDGET=5000 / MVT 5000 为基准线）。 */
export interface DensityThresholds {
  /** ≥ 此值点层自动切聚合表达。 */
  cluster: number;
  /** ≥ 此值点层切热力表达（隐含聚合）。 */
  heatmap: number;
  /** 线层密度降档阈值（宽度递减 + 简化证据）。 */
  lineDensify: number;
}

export const DEFAULT_DENSITY_THRESHOLDS: DensityThresholds = {
  cluster: 5000,
  heatmap: 20000,
  lineDensify: 5000,
};

/** 出厂默认值表 — 全部可被调用方逐项覆盖（显式用户意图永远赢过符号律）。 */
export interface SymbolLawDefaults {
  circle: {
    /** 密度无关的锚点半径（替换旧硬编码 6）。 */
    radiusAnchor: number;
    /** zoom 停靠点 [zoom, scale]，scale 乘在锚点上。 */
    radiusZoomScale: Array<[number, number]>;
    radiusMin: number;
    radiusMax: number;
    /** count → 锚点缩放（密度越高点径越小），按桶取值。 */
    radiusCountScale: Array<{ above: number; scale: number }>;
  };
  line: {
    widthAnchor: number;
    widthZoomScale: Array<[number, number]>;
    widthMin: number;
    widthMax: number;
    widthCountScale: Array<{ above: number; scale: number }>;
  };
  heatmap: {
    /** 密度无关基础半径（resolveHeatmapRadiusPx 的缺省锚点）。 */
    radiusAnchor: number;
    radiusMin: number;
    radiusMax: number;
    radiusZoomScale: Array<[number, number]>;
    radiusCountScale: Array<{ above: number; scale: number }>;
  };
  /** 不透明度锚点（替换旧硬编码 0.8）与密度下修表。 */
  opacity: {
    anchor: number;
    countDownshift: Array<{ above: number; value: number }>;
  };
}

export const DEFAULT_SYMBOL_LAW: SymbolLawDefaults = {
  circle: {
    radiusAnchor: 6,
    // 放大后点径平滑增大（旧实现是常量 6 ——「放大后点还是那么大」的痛点）。
    radiusZoomScale: [
      [4, 0.7],
      [8, 1.0],
      [12, 1.35],
      [16, 1.8],
    ],
    radiusMin: 2,
    radiusMax: 40,
    // 挤成一团时点径递减（§1 痛点 2）。
    radiusCountScale: [
      { above: 1000, scale: 0.85 },
      { above: 5000, scale: 0.7 },
      { above: 20000, scale: 0.55 },
    ],
  },
  line: {
    widthAnchor: 1.5,
    widthZoomScale: [
      [4, 0.7],
      [10, 1.0],
      [16, 1.6],
    ],
    widthMin: 0.5,
    widthMax: 24,
    widthCountScale: [
      { above: 5000, scale: 0.8 },
      { above: 20000, scale: 0.6 },
    ],
  },
  heatmap: {
    radiusAnchor: 30,
    radiusMin: 4,
    radiusMax: 80,
    // 半径随 zoom 平滑放大（旧实现是常量 px —— 放大后热团不展开）。
    radiusZoomScale: [
      [3, 0.6],
      [8, 1.0],
      [12, 1.35],
      [16, 1.7],
    ],
    radiusCountScale: [
      { above: 20000, scale: 0.85 },
      { above: 50000, scale: 0.7 },
    ],
  },
  opacity: {
    anchor: 0.8,
    // 密度越高不透明度越低（叠加发黑缓解）。
    countDownshift: [
      { above: 5000, value: 0.65 },
      { above: 20000, value: 0.5 },
    ],
  },
};

// ─── density signal（全仓唯一份；05 线 import 消费）──────────────────────────

export interface DensitySignal {
  level: DensityLevel;
  /** 归一化对数密度分（0 稀疏 → 1 极密），单调于 featureCount。 */
  score: number;
  featureCount: number;
}

const DENSITY_LEVEL_CUTS: Array<{ above: number; level: DensityLevel }> = [
  { above: 0, level: "sparse" },
  { above: 1000, level: "normal" },
  { above: DEFAULT_DENSITY_THRESHOLDS.cluster, level: "dense" },
  { above: DEFAULT_DENSITY_THRESHOLDS.heatmap, level: "very-dense" },
];

/**
 * The single density signal for adaptive symbology AND adaptive labels.
 * `featureCount <= 0` / non-finite → "sparse" with score 0 (unknown data
 * renders exactly like the pre-law defaults).
 */
export function densitySignal(featureCount: number): DensitySignal {
  const count = Number.isFinite(featureCount) && featureCount > 0 ? featureCount : 0;
  let level: DensityLevel = "sparse";
  for (const cut of DENSITY_LEVEL_CUTS) {
    if (count >= cut.above) level = cut.level;
  }
  // log10 归一：1 → 0，100k+ → 1（5 个数量级窗口）。
  const score = count <= 1 ? 0 : Math.min(1, Math.log10(count) / 5);
  return { level, score, featureCount: count };
}

/** 05 线协调点别名（§0.5：谁先落地对方 import；本线先落地，导出 snake_case 契约名）。 */
export const density_signal = densitySignal;

// ─── 内部工具 ────────────────────────────────────────────────────────────────

function countScale(count: number, table: Array<{ above: number; scale: number }>): number {
  let scale = 1;
  for (const row of table) {
    if (count >= row.above) scale = row.scale;
  }
  return scale;
}

function round3(v: number): number {
  return Math.round(v * 1000) / 1000;
}

function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

function scaleStops(
  anchor: number,
  stops: Array<[number, number]>,
): Array<[number, number]> {
  return stops.map(([z, s]) => [z, round3(anchor * s)]);
}

/**
 * MapLibre zoom-interpolate over scaled stops, with the FIRST stop value
 * additionally folded in as the expression fallback (zoom expressions in
 * paint properties are evaluated with the camera, no data fallback needed;
 * keeping stop[0] below any realistic zoom guarantees monotone interpolation).
 */
function zoomInterpolate(stops: Array<[number, number]>): unknown[] {
  const flat: unknown[] = ["interpolate", ["linear"], ["zoom"]];
  for (const [z, v] of stops) flat.push(z, v);
  return flat;
}

// ─── 表达式构造（P1 核心）────────────────────────────────────────────────────

export interface RadiusInputs {
  /** 渲染时相机 zoom —— 仅用于 JS 侧取值断言/测试；表达式本身按 zoom 插值。 */
  zoom?: number;
  featureCount?: number;
  overrides?: Partial<SymbolLawDefaults["circle"]>;
}

/**
 * 点径符号律：`f(zoom, featureCount)` → zoom interpolate 表达式。
 * 替换 renderer.ts:417 的硬编码 `circle-radius: 6`。
 */
export function circleRadiusExpression(inputs: RadiusInputs = {}): unknown[] {
  const law = { ...DEFAULT_SYMBOL_LAW.circle, ...inputs.overrides };
  const count = Number.isFinite(inputs.featureCount) ? Math.max(0, inputs.featureCount as number) : 0;
  const anchor = clamp(law.radiusAnchor * countScale(count, law.radiusCountScale), law.radiusMin, law.radiusMax);
  const stops = scaleStops(anchor, law.radiusZoomScale).map(
    ([z, v]) => [z, clamp(v, law.radiusMin, law.radiusMax)] as [number, number],
  );
  return zoomInterpolate(stops);
}

export interface WidthInputs {
  zoom?: number;
  featureCount?: number;
  overrides?: Partial<SymbolLawDefaults["line"]>;
}

/** 线宽符号律：随 zoom 缓增、随密度递减。替换 updateLayerStyle 之外的隐式常量。 */
export function lineWidthExpression(inputs: WidthInputs = {}): unknown[] {
  const law = { ...DEFAULT_SYMBOL_LAW.line, ...inputs.overrides };
  const count = Number.isFinite(inputs.featureCount) ? Math.max(0, inputs.featureCount as number) : 0;
  const anchor = clamp(law.widthAnchor * countScale(count, law.widthCountScale), law.widthMin, law.widthMax);
  const stops = scaleStops(anchor, law.widthZoomScale).map(
    ([z, v]) => [z, clamp(v, law.widthMin, law.widthMax)] as [number, number],
  );
  return zoomInterpolate(stops);
}

export interface HeatmapRadiusInputs {
  featureCount?: number;
  /** 契约锚点（resolveHeatmapRadiusPx 产物的 px 值）；缺省用出厂默认 30。 */
  baseRadiusPx?: number;
  overrides?: Partial<SymbolLawDefaults["heatmap"]>;
}

/**
 * 热力半径符号律：契约半径（resolveHeatmapRadiusPx，显式意图优先）为 zoom=8
 * 的锚点，随 zoom 平滑展开、随密度收缩。替换 `heatmap-radius: <const px>`。
 */
export function heatmapRadiusExpression(inputs: HeatmapRadiusInputs = {}): unknown[] {
  const law = { ...DEFAULT_SYMBOL_LAW.heatmap, ...inputs.overrides };
  const count = Number.isFinite(inputs.featureCount) ? Math.max(0, inputs.featureCount as number) : 0;
  const base = Number.isFinite(inputs.baseRadiusPx)
    ? clamp(Math.floor(inputs.baseRadiusPx as number), law.radiusMin, law.radiusMax)
    : law.radiusAnchor;
  const anchor = clamp(base * countScale(count, law.radiusCountScale), law.radiusMin, law.radiusMax);
  const stops = scaleStops(anchor, law.radiusZoomScale).map(([z, v]) => [z, clamp(v, law.radiusMin, law.radiusMax)] as [number, number]);
  return zoomInterpolate(stops);
}

export interface OpacityInputs {
  featureCount?: number;
  overrides?: Partial<SymbolLawDefaults["opacity"]>;
}

/**
 * 不透明度符号律：密度越高越透明（旧硬编码 0.8 与要素数无关）。
 * 返回常量数（不透明度无需 zoom 维度；密度在编译期已知）。
 */
export function opacityForCount(inputs: OpacityInputs = {}): number {
  const law = { ...DEFAULT_SYMBOL_LAW.opacity, ...inputs.overrides };
  const count = Number.isFinite(inputs.featureCount) ? Math.max(0, inputs.featureCount as number) : 0;
  let value = law.anchor;
  for (const row of law.countDownshift) {
    if (count >= row.above) value = row.value;
  }
  return value;
}

/** 描边宽符号律：点/面描边随 zoom 微增（无密度维度 —— 描边是可读性语义）。 */
export function strokeWidthExpression(anchor = 1.5): unknown[] {
  return zoomInterpolate(scaleStops(anchor, DEFAULT_SYMBOL_LAW.line.widthZoomScale));
}

// ─── 密度自适应表达切换（P2）────────────────────────────────────────────────

export type DensityPresentationMode = "native" | "cluster" | "heatmap";

export interface DensityPresentation {
  mode: DensityPresentationMode;
  /** 触发依据（进 evidence；人类可读）。 */
  reason: string;
  thresholds: DensityThresholds;
}

export interface PresentationInputs {
  geometryType: SymbolGeometryType;
  featureCount: number;
  thresholds?: Partial<DensityThresholds>;
  /** 数据源已显式配置聚合时返回 native（显式意图优先）。 */
  explicitCluster?: boolean;
}

/**
 * 密度自适应切换裁决：点 → 聚合(≥cluster)/热力(≥heatmap)；线 → native +
 * 降档理由（几何简化由 MVT 通道承担，前端侧降线宽并记 evidence）。
 * 面不变（面密度语义由分类/抽稀承担）。
 */
export function resolveDensityPresentation(inputs: PresentationInputs): DensityPresentation {
  const t = { ...DEFAULT_DENSITY_THRESHOLDS, ...inputs.thresholds };
  const count = Number.isFinite(inputs.featureCount) ? Math.max(0, inputs.featureCount) : 0;
  if (inputs.geometryType === "Point" && !inputs.explicitCluster) {
    if (count >= t.heatmap) {
      return { mode: "heatmap", reason: `point_count ${count} >= heatmap threshold ${t.heatmap}`, thresholds: t };
    }
    if (count >= t.cluster) {
      return { mode: "cluster", reason: `point_count ${count} >= cluster threshold ${t.cluster}`, thresholds: t };
    }
  }
  if (inputs.geometryType === "LineString" && count >= t.lineDensify) {
    return {
      mode: "native",
      reason: `line_count ${count} >= line threshold ${t.lineDensify} — width downscale + MVT simplification channel`,
      thresholds: t,
    };
  }
  return { mode: "native", reason: `count ${count} below adaptation thresholds`, thresholds: t };
}

// ─── 运行时 evidence（有界环 + 计数；一切「静默丢弃」的替代出口）────────────

export type SymbolLawEventKind =
  | "law-applied"
  | "density-switch"
  | "incremental-fallback"
  | "unmapped-paint-key"
  | "unknown-source-type"
  | "presentation-decision"
  | "legend-v2-metadata";

export interface SymbolLawEvent {
  kind: SymbolLawEventKind;
  /** 层/源 id（可省）。 */
  id?: string;
  at: number;
  detail: Record<string, unknown>;
}

const EVIDENCE_CAPACITY = 64;
const evidenceRing: SymbolLawEvent[] = [];
const evidenceCounts: Record<string, number> = {};
let evidenceSeq = 0;

/** 记录一条 evidence（有界：覆盖最旧；计数器无界但仅 number）。 */
export function recordSymbolLawEvidence(
  kind: SymbolLawEventKind,
  detail: Record<string, unknown> = {},
  id?: string,
): void {
  evidenceSeq += 1;
  evidenceCounts[kind] = (evidenceCounts[kind] ?? 0) + 1;
  evidenceRing.push({ kind, id, at: evidenceSeq, detail });
  if (evidenceRing.length > EVIDENCE_CAPACITY) evidenceRing.shift();
}

/** 快照（测试与观测读；不进生产热路径的读侧）。 */
export function getSymbolLawEvidence(): {
  events: SymbolLawEvent[];
  counts: Record<string, number>;
} {
  return { events: [...evidenceRing], counts: { ...evidenceCounts } };
}

/** 测试专用清空。 */
export function resetSymbolLawEvidence(): void {
  evidenceRing.length = 0;
  for (const k of Object.keys(evidenceCounts)) delete evidenceCounts[k];
  evidenceSeq = 0;
}
