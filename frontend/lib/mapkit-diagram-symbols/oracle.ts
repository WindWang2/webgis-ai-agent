/**
 * oracle.ts —— Python ``app/lib/cartography/diagram_symbols/geometry.py`` +
 * ``contracts.py`` 的 TS 位级孪生（WP3 渲染通道的唯一几何权威）。
 *
 * 角度约定（本域冻结）：0° = 12 点钟方向，顺时针为正，单位度，域 [0, 360)。
 * 条形约定：glyph-local，y 轴向上为正，基线 y=0（GL 屏幕空间渲染时由
 * vertices.ts 翻转）。所有输出坐标/角度/半径只量化一次（3 位小数，
 * ``quantize3``），消费方与测试按同一精度复核，不得二次 round。
 *
 * parity 纪律：本模块输出与 golden fixtures
 * （./fixtures/oracle-fixtures.json）逐位相等（含 3 位小数量化后的浮点
 * 严格相等）；错误 code 与 Python DIAGRAM_ERROR_CODES 对齐。
 */

import {
  canonicalJson,
  fsum,
  pyFloat,
  pyInt,
  sha256Hex,
  type CanonicalValue,
} from "./canonical";

// ── 常量（与 contracts.py 逐字对齐）──────────────────────────────────

export const GEOMETRY_DECIMALS = 3;

export const DIAGRAM_SYMBOL_KINDS = [
  "composition_pie",
  "composition_donut",
  "series_bars",
  "segmented_proportional",
] as const;
export type DiagramSymbolKind = (typeof DIAGRAM_SYMBOL_KINDS)[number];

export const SERIES_BAR_LAYOUTS = ["stacked", "grouped"] as const;
export type SeriesBarLayout = (typeof SERIES_BAR_LAYOUTS)[number];

export const MISSING_POLICIES = ["omit", "block"] as const;

export const MAX_CATEGORIES = 64;
export const MIN_CATEGORIES = 2;
export const MIN_COMPOSITION_CATEGORIES = 2;

/** scale 跨度上限：max/min 有限值比超过该值 → EXTREME_VALUE_RANGE。 */
export const EXTREME_VALUE_RANGE_RATIO = 10_000.0;
/** 半径比上限（sqrt 后仍 >100 倍）。 */
export const EXTREME_RADIUS_RATIO = 100.0;
/** 图符几何输出尺寸上限（px @1x）。 */
export const MAX_GLYPH_RADIUS_PX = 120.0;
export const MAX_GLYPH_EXTENT_PX = 240.0;

// ── typed 错误 ───────────────────────────────────────────────────────

export type DiagramErrorCode =
  | "UNKNOWN_KIND"
  | "UNKNOWN_LAYOUT"
  | "UNKNOWN_MISSING_POLICY"
  | "TOO_FEW_CATEGORIES"
  | "TOO_MANY_CATEGORIES"
  | "FIELD_NOT_FOUND"
  | "FIELD_NOT_NUMERIC"
  | "NEGATIVE_VALUE"
  | "NON_FINITE_VALUE"
  | "ZERO_TOTAL"
  | "EXTREME_VALUE_RANGE"
  | "INVALID_SIZE_POLICY"
  | "MIXED_UNITS"
  | "MISSING_UNIT_DECLARATION"
  | "DUPLICATE_CATEGORY"
  | "INNER_RADIUS_OUT_OF_RANGE"
  | "INVALID_COORDINATES"
  | "MISSING_VALUE_BLOCKED"
  | "BUDGET_EXCEEDED";

export interface DiagramErrorOptions {
  field?: string;
  correctionHint?: string;
  details?: Record<string, unknown>;
}

/** 图符域 typed 错误：code + message + 可执行修正建议（与 Python 同构）。 */
export class DiagramSymbolError extends Error {
  readonly code: DiagramErrorCode | string;
  readonly field: string | null;
  readonly correctionHint: string;
  readonly details: Readonly<Record<string, unknown>>;

  constructor(code: DiagramErrorCode | string, message: string, options: DiagramErrorOptions = {}) {
    super(message);
    this.name = "DiagramSymbolError";
    this.code = code;
    this.field = options.field ?? null;
    this.correctionHint = options.correctionHint ?? "";
    this.details = options.details ?? {};
  }

  /** 有界、可机读的错误回执（不含内部栈/路径）。 */
  payload(): Record<string, unknown> {
    const out: Record<string, unknown> = { success: false, code: this.code, message: this.message };
    if (this.field) out.field = this.field;
    if (this.correctionHint) out.correction_hint = this.correctionHint;
    if (Object.keys(this.details).length > 0) out.details = this.details;
    return out;
  }
}

// ── 量化（唯一量化点）────────────────────────────────────────────────

const roundBits = new DataView(new ArrayBuffer(8));

/** double 分解为精确二进有理数 m·2^e（m 为带符号 BigInt 整数）。 */
function decomposeDouble(v: number): { m: bigint; e: number } {
  roundBits.setFloat64(0, v);
  const hi = roundBits.getUint32(0);
  const lo = roundBits.getUint32(4);
  const biased = (hi >>> 20) & 0x7ff;
  if (biased === 0) {
    const m = BigInt(lo) | (BigInt(hi & 0xfffff) << BigInt(32));
    return { m, e: -1074 };
  }
  const m = BigInt(lo) | (BigInt((hi & 0xfffff) | 0x100000) << BigInt(32));
  return { m: hi >>> 31 ? -m : m, e: biased - 1075 };
}

/**
 * 精确比较 double v 的精确值与有理数 num/(2^pow2·odd)（均可为符号数）。
 * 返回 -1 / 0 / 1。v = m·2^e ⟺ 直接符号比较 m·odd·2^(e+pow2) vs num。
 */
function cmpVsRational(v: number, num: bigint, pow2: number, odd: bigint): number {
  const { m, e } = decomposeDouble(v);
  const lhs = m * odd;
  const shift = e + pow2;
  if (shift >= 0) {
    const l = lhs << BigInt(shift);
    return l < num ? -1 : l > num ? 1 : 0;
  }
  const r = num << BigInt(-shift);
  return lhs < r ? -1 : lhs > r ? 1 : 0;
}

/**
 * Python ``round(v, ndigits)`` 的精确孪生：对 double 的**精确二进值**做
 * 十进制 half-even 舍入。任务书原口径 Math.round(v*1000)/1000 在乘法二重
 * 舍入的半途边界（如 displaced = (24.333+2)/2 = 13.1665 的最近 double）
 * 与 Python 相差 1 mil px —— 随机差分语料实测捕获，故升级为此精确实现
 * （fixtures 无边界 case，结果不变；随机语料逐位一致）。
 */
export function pythonRound(v: number, ndigits: number): number {
  if (!Number.isFinite(v)) return v;
  if (v === 0) return 0;
  const factor = Math.pow(10, ndigits);
  const approx = v * factor;
  if (!Number.isFinite(approx)) return v; // 量级超出 → 十进制不可表示，Python 返回原值
  const k0 = Math.round(approx);
  // 精确中点比较：v·factor vs k±0.5 ⟺ v vs (2k±1)/(2·10^n) = (2k±1)/(2^(n+1)·5^n)
  const denPow2 = ndigits + 1;
  const denOdd = BigInt(5) ** BigInt(ndigits);
  const atLower = cmpVsRational(v, BigInt(2 * k0 - 1), denPow2, denOdd);
  const atUpper = cmpVsRational(v, BigInt(2 * k0 + 1), denPow2, denOdd);
  let k = k0;
  if (atLower < 0) {
    k = k0 - 1;
  } else if (atLower === 0) {
    k = (k0 - 1) % 2 === 0 ? k0 - 1 : k0; // half-even
  } else if (atUpper > 0) {
    k = k0 + 1;
  } else if (atUpper === 0) {
    k = (k0 + 1) % 2 === 0 ? k0 + 1 : k0; // half-even
  }
  const r = k / factor;
  return r === 0 ? 0 : r;
}

/**
 * 量化到 3 位小数（一次性；-0 归 +0 保证 canonical 稳定）。
 * 与 Python ``_q(v) = round(float(v), 3)`` 逐位一致（含 half-even 与
 * double 精确值边界 —— 见 pythonRound）。
 */
export function quantize3(value: number): number {
  return pythonRound(value, 3);
}

// ── 类型 ─────────────────────────────────────────────────────────────

/** (category, value) 类别序对。 */
export type GlyphValue = readonly [category: string, value: number];

/** scale 行：资格层 (category, value) 对，或裸数值。 */
export type ScaleCell = number | readonly [category: string, value: number];
export type ValueRow = readonly ScaleCell[];

export interface SizePolicyInput {
  r_max_px?: number;
  inner_radius_ratio?: number;
  total_area_encoding?: boolean | number;
  r_min_px?: number;
  h_max_px?: number;
  bar_w_px?: number;
  bar_gap_px?: number;
}

/** normalize_size_policy 的输出（所有字段有限；可选键与 Python dict 一致）。 */
export interface NormalizedSizePolicy {
  r_max_px?: number;
  inner_radius_ratio?: number;
  total_area_encoding?: number;
  r_min_px?: number;
  h_max_px?: number;
  bar_w_px?: number;
  bar_gap_px?: number;
}

/** compute_glyph_scale 的输出（cross-feature common scale）。 */
export interface GlyphScale {
  r_max_px?: number;
  v_ref?: number;
  r_min_px?: number;
  total_area_encoding?: number;
  v_abs_max?: number;
  px_per_unit?: number;
  h_max_px?: number;
  bar_w_px?: number;
  bar_gap_px?: number;
}

export interface PieSlice {
  category: string;
  value: number;
  share: number;
  start_angle_deg: number;
  end_angle_deg: number;
}

export interface CompositionGlyph {
  kind: "composition_pie" | "composition_donut";
  radius_px: number;
  total: number;
  slices: PieSlice[];
  zero_slices: number;
  inner_radius_ratio?: number;
  inner_radius_px?: number;
  radius_clamped_to_min?: true;
}

export interface BarSegment {
  category: string;
  value: number;
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

export interface SeriesBarsGlyph {
  kind: "series_bars";
  layout: SeriesBarLayout;
  baseline_y: number;
  extent_px: { top: number; bottom: number };
  segments: BarSegment[];
  zero_values: number;
}

export interface SegmentedSegment {
  category: string;
  value: number;
  start_angle_deg: number;
  end_angle_deg: number;
  radius_px: number;
  area_px2: number;
}

export interface SegmentedGlyph {
  kind: "segmented_proportional";
  slot_deg: number;
  radius_px: number;
  segments: SegmentedSegment[];
  zero_segments: number;
}

export type DiagramGlyph = CompositionGlyph | SeriesBarsGlyph | SegmentedGlyph;

// ── 内部工具 ─────────────────────────────────────────────────────────

function requireScaleNumber(scale: GlyphScale, key: keyof GlyphScale): number {
  const v = scale[key];
  if (typeof v !== "number") {
    // 对齐 Python KeyError：缺 scale 键是编程错误而非域错误。
    throw new TypeError(`scale.${String(key)} 缺失 —— 请先调用 compute_glyph_scale 产出 scale`);
  }
  return v;
}

function requirePolicyNumber(
  policy: NormalizedSizePolicy,
  key: keyof NormalizedSizePolicy,
): number {
  const v = policy[key];
  if (typeof v !== "number") {
    throw new TypeError(`size_policy.${String(key)} 缺失 —— 请先调用 normalize_size_policy`);
  }
  return v;
}

function checkedValue(value: unknown, category: string, allowNegative: boolean): number {
  if (typeof value !== "number") {
    throw new DiagramSymbolError(
      "FIELD_NOT_NUMERIC",
      `类别 ${JSON.stringify(category)} 的值必须是数值`,
      { field: category },
    );
  }
  if (!Number.isFinite(value)) {
    throw new DiagramSymbolError(
      "NON_FINITE_VALUE",
      `类别 ${JSON.stringify(category)} 的值非有限（NaN/Inf）`,
      { field: category, correctionHint: "缺失值应走 missing_policy，而不是以 NaN 参与几何" },
    );
  }
  if (!allowNegative && value < 0) {
    throw new DiagramSymbolError(
      "NEGATIVE_VALUE",
      `类别 ${JSON.stringify(category)} 的值为负（${value}）：组成比例/分段面积语义不接受负值`,
      { field: category, correctionHint: "负值请改用 series_bars（0 基线长度语义）" },
    );
  }
  return value;
}

function finitePositive(value: unknown, field: string): number {
  if (typeof value !== "number") {
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `${field} 必须是数值`,
      { field },
    );
  }
  if (!Number.isFinite(value) || value <= 0) {
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `${field} 必须是有限正数，got ${String(value)}`,
      { field, correctionHint: "0 与 NaN/Inf 均拒绝" },
    );
  }
  return value;
}

function finiteNonNegative(value: unknown, field: string): number {
  if (typeof value !== "number") {
    throw new DiagramSymbolError("INVALID_SIZE_POLICY", `${field} 必须是数值`, { field });
  }
  if (!Number.isFinite(value) || value < 0) {
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `${field} 必须是有限非负数，got ${String(value)}`,
      { field },
    );
  }
  return value;
}

// ── 尺寸政策 ─────────────────────────────────────────────────────────

/**
 * 归一化尺寸政策为带缺省的 typed dict（所有输出为有限正 float）。
 * 缺省：composition/segmented r_max_px=32；donut inner_radius_ratio=0.55；
 * series_bars h_max_px=40、bar_w_px=8、bar_gap_px=1；
 * total_area_encoding（仅 composition 族）缺省关闭。
 */
export function normalizeSizePolicy(
  kind: string,
  sizePolicy?: SizePolicyInput | null,
): NormalizedSizePolicy {
  const sp: SizePolicyInput = sizePolicy ?? {};
  const normalized: NormalizedSizePolicy = {};
  if (
    kind === "composition_pie" || kind === "composition_donut" || kind === "segmented_proportional"
  ) {
    const rMax = finitePositive(sp.r_max_px ?? 32.0, "size_policy.r_max_px");
    if (rMax > MAX_GLYPH_RADIUS_PX) {
      throw new DiagramSymbolError(
        "INVALID_SIZE_POLICY",
        `r_max_px=${rMax} 超过图符半径上限 ${MAX_GLYPH_RADIUS_PX}px（单图符路径有界）`,
        {
          field: "size_policy.r_max_px",
          correctionHint: `取 r_max_px ∈ (0, ${MAX_GLYPH_RADIUS_PX}]`,
        },
      );
    }
    normalized.r_max_px = rMax;
    if (kind === "composition_donut") {
      const ratio = sp.inner_radius_ratio;
      if (typeof ratio !== "number") {
        throw new DiagramSymbolError(
          "INVALID_SIZE_POLICY",
          "inner_radius_ratio 必须是数值",
          { field: "size_policy.inner_radius_ratio" },
        );
      }
      if (!(ratio > 0.0 && ratio < 1.0)) {
        throw new DiagramSymbolError(
          "INNER_RADIUS_OUT_OF_RANGE",
          `inner_radius_ratio=${ratio} 不在 (0,1)（0=退化成 pie，1=无环身）`,
          {
            field: "size_policy.inner_radius_ratio",
            correctionHint: "取 (0,1) 开区间，如 0.55",
          },
        );
      }
      normalized.inner_radius_ratio = ratio;
    }
    if (Boolean(sp.total_area_encoding)) {
      normalized.total_area_encoding = 1.0;
      const rMinDefault = Math.max(2.0, rMax * 0.15);
      const rMin = finitePositive(sp.r_min_px ?? rMinDefault, "size_policy.r_min_px");
      normalized.r_min_px = rMin;
      if (normalized.r_min_px > rMax) {
        throw new DiagramSymbolError(
          "INVALID_SIZE_POLICY",
          `r_min_px=${normalized.r_min_px} > r_max_px=${rMax}`,
          { field: "size_policy.r_min_px" },
        );
      }
    }
  } else if (kind === "series_bars") {
    const hMax = finitePositive(sp.h_max_px ?? 40.0, "size_policy.h_max_px");
    if (hMax > MAX_GLYPH_EXTENT_PX / 2) {
      throw new DiagramSymbolError(
        "INVALID_SIZE_POLICY",
        `h_max_px=${hMax} 超过图符半幅上限 ${MAX_GLYPH_EXTENT_PX / 2}px`,
        { field: "size_policy.h_max_px" },
      );
    }
    normalized.h_max_px = hMax;
    normalized.bar_w_px = finitePositive(sp.bar_w_px ?? 8.0, "size_policy.bar_w_px");
    normalized.bar_gap_px = finiteNonNegative(sp.bar_gap_px ?? 1.0, "size_policy.bar_gap_px");
  } else {
    throw new DiagramSymbolError(
      "UNKNOWN_KIND",
      `未知图符 kind=${JSON.stringify(kind)}`,
      { correctionHint: `可选：${DIAGRAM_SYMBOL_KINDS.join(" | ")}` },
    );
  }
  return normalized;
}

// ── 跨要素公共尺度 ───────────────────────────────────────────────────

/** 行归一：接受裸数值 [v, ...] 或 (category, value) 对 [(c, v), ...]。 */
export function rowNumbers(row: ValueRow): number[] {
  const out: number[] = [];
  for (const item of row) {
    if (Array.isArray(item) && item.length === 2) {
      out.push(Number(item[1]));
    } else {
      out.push(Number(item));
    }
  }
  return out;
}

/**
 * 计算全图唯一公共尺度（cross-feature common scale）。
 * - composition（total_area_encoding 开）：v_ref = 跨要素总量最大值；
 * - composition（关）/ segmented：v_ref = 全图 (要素,类别) 单值最大；
 * - series_bars：v_abs_max + px_per_unit = h_max / v_abs_max。
 * 极端跨度：max/min 比 > EXTREME_VALUE_RANGE_RATIO → typed 拒绝。
 */
export function computeGlyphScale(
  kind: string,
  perFeatureValues: readonly ValueRow[],
  sizePolicy: NormalizedSizePolicy,
): GlyphScale {
  const flat: number[] = [];
  for (const row of perFeatureValues) flat.push(...rowNumbers(row));
  if (flat.length === 0) {
    throw new DiagramSymbolError(
      "ZERO_TOTAL",
      "没有任何可用数值（全要素全类别为空）",
      { correctionHint: "检查字段选择与缺失策略" },
    );
  }
  const positives = flat.filter((v) => v > 0);
  const absolutes = flat.map((v) => Math.abs(v));
  const vMaxAbs = absolutes.length > 0 ? Math.max(...absolutes) : 0;
  const scale: GlyphScale = {};
  if (
    kind === "composition_pie" || kind === "composition_donut" || kind === "segmented_proportional"
  ) {
    if (positives.length === 0) {
      throw new DiagramSymbolError(
        "ZERO_TOTAL",
        "全部数值为零/非正，无面积或角度语义可编码",
        { correctionHint: "检查数据是否全为零值" },
      );
    }
    const rMax = requirePolicyNumber(sizePolicy, "r_max_px");
    scale.r_max_px = rMax;
    if (
      (kind === "composition_pie" || kind === "composition_donut") &&
      Boolean(sizePolicy.total_area_encoding)
    ) {
      // 总量面积编码：半径律作用在要素总量上 → v_ref = 跨要素总量最大值。
      const totals = perFeatureValues
        .map((row) => fsum(rowNumbers(row)))
        .filter((t) => t > 0);
      if (totals.length === 0) {
        throw new DiagramSymbolError(
          "ZERO_TOTAL",
          "全部要素总量为零，无总量面积语义可编码",
          { correctionHint: "关闭 total_area_encoding（只编码组成）或检查数据" },
        );
      }
      const vMaxTotal = Math.max(...totals);
      const vMinTotal = Math.min(...totals.filter((t) => t > 0));
      if (vMaxTotal / vMinTotal > EXTREME_VALUE_RANGE_RATIO) {
        throw new DiagramSymbolError(
          "EXTREME_VALUE_RANGE",
          `总量跨度 max/min=${(vMaxTotal / vMinTotal).toPrecision(3)} 超过 ` +
            `${EXTREME_VALUE_RANGE_RATIO}，最小图符半径比将 >${EXTREME_RADIUS_RATIO} 倍，视觉不可辨`,
          {
            correctionHint: "先分档或关闭总量面积编码",
            details: { v_max_total: vMaxTotal, v_min_total: vMinTotal },
          },
        );
      }
      scale.v_ref = vMaxTotal;
      scale.total_area_encoding = 1.0;
      scale.r_min_px = requirePolicyNumber(sizePolicy, "r_min_px");
    } else {
      // segmented 与未开总量编码的组成：v_ref = 全图 (要素,类别) 单值最大。
      const vMinPos = Math.min(...positives);
      const vMaxPos = Math.max(...positives);
      if (vMaxPos / vMinPos > EXTREME_VALUE_RANGE_RATIO) {
        throw new DiagramSymbolError(
          "EXTREME_VALUE_RANGE",
          `数值跨度 max/min=${(vMaxPos / vMinPos).toPrecision(3)} 超过 ` +
            `${EXTREME_VALUE_RANGE_RATIO}，最小图符半径比将 >${EXTREME_RADIUS_RATIO} 倍，视觉不可辨`,
          {
            correctionHint: "先分档（graduated）或改用对数语义表达，再考虑图符",
            details: { v_max: vMaxPos, v_min: vMinPos },
          },
        );
      }
      scale.v_ref = vMaxPos;
      if (Boolean(sizePolicy.total_area_encoding)) {
        scale.total_area_encoding = 1.0;
        scale.r_min_px = requirePolicyNumber(sizePolicy, "r_min_px");
      }
    }
  } else if (kind === "series_bars") {
    if (!(vMaxAbs > 0)) {
      throw new DiagramSymbolError(
        "ZERO_TOTAL",
        "全部数值为零，条形无长度语义",
        { correctionHint: "检查数据" },
      );
    }
    const posVals = absolutes.filter((v) => v > 0);
    const vMinAbs = Math.min(...posVals);
    if (vMaxAbs / vMinAbs > EXTREME_VALUE_RANGE_RATIO) {
      throw new DiagramSymbolError(
        "EXTREME_VALUE_RANGE",
        `数值跨度 max/min=${(vMaxAbs / vMinAbs).toPrecision(3)} 超过 ` +
          `${EXTREME_VALUE_RANGE_RATIO}，最小条形长度不可辨`,
        { correctionHint: "先分档或拆层表达", details: { v_max: vMaxAbs, v_min: vMinAbs } },
      );
    }
    scale.v_abs_max = vMaxAbs;
    scale.px_per_unit = requirePolicyNumber(sizePolicy, "h_max_px") / vMaxAbs;
    scale.h_max_px = requirePolicyNumber(sizePolicy, "h_max_px");
    scale.bar_w_px = requirePolicyNumber(sizePolicy, "bar_w_px");
    scale.bar_gap_px = sizePolicy.bar_gap_px ?? 1.0;
  } else {
    throw new DiagramSymbolError("UNKNOWN_KIND", `未知图符 kind=${JSON.stringify(kind)}`);
  }
  return scale;
}

// ── 组成比例（pie / donut）───────────────────────────────────────────

function buildCompositionGlyph(
  values: readonly GlyphValue[],
  scale: GlyphScale,
  sizePolicy: SizePolicyInput | null | undefined,
  kind: "composition_pie" | "composition_donut",
): CompositionGlyph {
  const sp = normalizeSizePolicy(kind, sizePolicy);
  const checked = values.map(
    ([cat, v]) => [cat, checkedValue(v, cat, false)] as const,
  );
  const total = fsum(checked.map(([, v]) => v));
  if (!(total > 0)) {
    throw new DiagramSymbolError(
      "ZERO_TOTAL",
      `组成比例分母 Σ=${total} 必须 > 0（分母显式 = 参算类别之和）`,
      {
        correctionHint: "检查是否全为零/缺失被剔除",
        details: { participating_categories: checked.length },
      },
    );
  }
  const rMax = requirePolicyNumber(sp, "r_max_px");
  let radius = rMax;
  let clamped = false;
  if (scale.total_area_encoding && scale.v_ref !== undefined) {
    const raw = rMax * Math.sqrt(total / scale.v_ref);
    const rMin = scale.r_min_px ?? 0.0;
    if (raw < rMin) {
      radius = rMin;
      clamped = true;
    } else {
      radius = raw;
    }
  }
  const slices: PieSlice[] = [];
  let acc = 0.0;
  let zeroSlices = 0;
  for (const [cat, v] of checked) {
    const share = v / total;
    const start = acc * 360.0;
    acc += share;
    const end = acc * 360.0;
    if (share === 0.0) zeroSlices += 1;
    slices.push({
      category: cat,
      value: quantize3(v),
      share: quantize3(share),
      start_angle_deg: quantize3(start),
      end_angle_deg: quantize3(end),
    });
  }
  const glyph: CompositionGlyph = {
    kind,
    radius_px: quantize3(radius),
    total: quantize3(total),
    slices,
    zero_slices: zeroSlices,
  };
  if (kind === "composition_donut") {
    const ratio = requirePolicyNumber(sp, "inner_radius_ratio");
    glyph.inner_radius_ratio = ratio;
    glyph.inner_radius_px = quantize3(radius * ratio);
  }
  if (clamped) glyph.radius_clamped_to_min = true;
  return glyph;
}

// ── 同量纲多序列条形（stacked / grouped）─────────────────────────────

function buildSeriesBarsGlyph(
  values: readonly GlyphValue[],
  scale: GlyphScale,
  sizePolicy: SizePolicyInput | null | undefined,
  layout: SeriesBarLayout,
): SeriesBarsGlyph {
  if (layout !== "stacked" && layout !== "grouped") {
    throw new DiagramSymbolError(
      "UNKNOWN_LAYOUT",
      `未知 series_bars 布局 ${JSON.stringify(layout)}`,
      { correctionHint: "可选：stacked | grouped" },
    );
  }
  const sp = normalizeSizePolicy("series_bars", sizePolicy);
  const checked = values.map(([cat, v]) => [cat, checkedValue(v, cat, true)] as const);
  const pxPerUnit = requireScaleNumber(scale, "px_per_unit");
  const barW = requirePolicyNumber(sp, "bar_w_px");
  const segments: BarSegment[] = [];
  let posRun = 0.0;
  let negRun = 0.0;
  let zeroValues = 0;
  let yTop = 0.0;
  let yBottom = 0.0;
  const n = checked.length;
  if (layout === "stacked") {
    const x0 = quantize3(-barW / 2.0);
    const x1 = quantize3(barW / 2.0);
    for (const [cat, v] of checked) {
      const length = v * pxPerUnit;
      let y0: number;
      let y1: number;
      if (v >= 0) {
        y0 = posRun;
        y1 = posRun + length;
        posRun += length;
      } else {
        y0 = negRun + length;
        y1 = negRun;
        negRun += length;
      }
      if (v === 0) zeroValues += 1;
      yTop = Math.max(yTop, y1);
      yBottom = Math.min(yBottom, y0);
      segments.push({
        category: cat,
        value: quantize3(v),
        x0,
        x1,
        y0: quantize3(y0),
        y1: quantize3(y1),
      });
    }
  } else {
    const gap = sp.bar_gap_px ?? 1.0;
    const totalW = n * barW + (n - 1) * gap;
    const left = -totalW / 2.0;
    for (let i = 0; i < n; i++) {
      const [cat, v] = checked[i];
      const gx0 = left + i * (barW + gap);
      const length = v * pxPerUnit;
      const y0 = v >= 0 ? 0.0 : length;
      const y1 = v >= 0 ? length : 0.0;
      if (v === 0) zeroValues += 1;
      yTop = Math.max(yTop, y1);
      yBottom = Math.min(yBottom, y0);
      segments.push({
        category: cat,
        value: quantize3(v),
        x0: quantize3(gx0),
        x1: quantize3(gx0 + barW),
        y0: quantize3(y0),
        y1: quantize3(y1),
      });
    }
  }
  return {
    kind: "series_bars",
    layout,
    baseline_y: 0.0,
    extent_px: { top: quantize3(yTop), bottom: quantize3(yBottom) },
    segments,
    zero_values: zeroValues,
  };
}

// ── 等角分段比例符号 ─────────────────────────────────────────────────

function buildSegmentedGlyph(
  values: readonly GlyphValue[],
  scale: GlyphScale,
  sizePolicy: SizePolicyInput | null | undefined,
): SegmentedGlyph {
  const sp = normalizeSizePolicy("segmented_proportional", sizePolicy);
  const checked = values.map(([cat, v]) => [cat, checkedValue(v, cat, false)] as const);
  const n = checked.length;
  if (n === 0) {
    // Python 360.0/0 会 ZeroDivisionError；此处给 typed 错误（已知偏差：
    // Python 走 validate_category_spec 的 TOO_FEW_CATEGORIES 挡在更上游）。
    throw new DiagramSymbolError("TOO_FEW_CATEGORIES", "segmented 图符至少需要 2 个类别");
  }
  const slotDeg = 360.0 / n;
  const rMax = requirePolicyNumber(sp, "r_max_px");
  const vRef = requireScaleNumber(scale, "v_ref");
  const segments: SegmentedSegment[] = [];
  let zeroSegments = 0;
  for (let i = 0; i < n; i++) {
    const [cat, v] = checked[i];
    const radius = rMax * Math.sqrt(v / vRef);
    if (radius === 0.0) zeroSegments += 1;
    const start = i * slotDeg;
    segments.push({
      category: cat,
      value: quantize3(v),
      start_angle_deg: quantize3(start),
      end_angle_deg: quantize3(start + slotDeg),
      radius_px: quantize3(radius),
      area_px2: quantize3((slotDeg / 360.0) * Math.PI * radius * radius),
    });
  }
  return {
    kind: "segmented_proportional",
    slot_deg: quantize3(slotDeg),
    radius_px: quantize3(rMax),
    segments,
    zero_segments: zeroSegments,
  };
}

// ── 统一入口 ─────────────────────────────────────────────────────────

export interface BuildGlyphOptions {
  layout?: SeriesBarLayout | string;
}

/** 按 kind 分派的唯一几何入口（所有通道都走这里 / Python 孪生）。 */
export function buildGlyphGeometry(
  kind: string,
  values: readonly GlyphValue[],
  scale: GlyphScale,
  sizePolicy: SizePolicyInput | null | undefined = null,
  options: BuildGlyphOptions = {},
): DiagramGlyph {
  if (kind === "composition_pie" || kind === "composition_donut") {
    return buildCompositionGlyph(values, scale, sizePolicy, kind);
  }
  if (kind === "series_bars") {
    const layout = options.layout ?? "stacked";
    return buildSeriesBarsGlyph(values, scale, sizePolicy, layout as SeriesBarLayout);
  }
  if (kind === "segmented_proportional") {
    return buildSegmentedGlyph(values, scale, sizePolicy);
  }
  throw new DiagramSymbolError("UNKNOWN_KIND", `未知图符 kind=${JSON.stringify(kind)}`, {
    correctionHint: "可选：composition_pie | composition_donut | series_bars | segmented_proportional",
  });
}

// ── hypot（Python math.hypot 的位级孪生）─────────────────────────────

/*
 * CPython 3.12 Modules/mathmodule.c ``vector_norm``（math.hypot 的实现）
 * 的忠实端口：lossless 2 的幂缩放 + Veltkamp-Dekker 精确平方 +
 * Neumaier 型补偿求和 + sqrt 差分修正。dl_mul 用 Dekker split 乘积
 * （与默认 fma 构建在常规域产出一模一样的 (hi, lo) 精确分解 ——
 * fma 不可用时的 UNRELIABLE_FMA 路径即此实现）。
 * V8 Math.hypot 是 naive sqrt，与 Python 相差 ~1/8 输入 1 ulp（实测），
 * 不可用。亚正规（subnormal）输入走 CPython 同款 DBL_MIN 重缩放分支。
 */

const DL_SPLIT_CONST = 134217729.0; // Veltkama 常量 2**27 + 1
const DBL_MIN = Math.pow(2, -1022);

const frexpView = new DataView(new ArrayBuffer(8));

/** C frexp 的指数半区（f = x / 2**e ∈ [0.5, 1)）；subnormal 返回哨兵 -1100。 */
function frexpExponent(x: number): number {
  frexpView.setFloat64(0, x);
  const highWord = frexpView.getUint32(0);
  const biased = (highWord >>> 20) & 0x7ff;
  if (biased === 0) return -1100; // subnormal → 走 DBL_MIN 重缩放分支
  return biased - 1022;
}

/** Dekker (5.12)：精确乘积分解 (z, zz)，z + zz == x*y。 */
function dlMul(x: number, y: number): [number, number] {
  const t = x * DL_SPLIT_CONST;
  const xHi = t - (t - x);
  const xLo = x - xHi;
  const u = y * DL_SPLIT_CONST;
  const yHi = u - (u - y);
  const yLo = y - yHi;
  const p = xHi * yHi;
  const q = xHi * yLo + xLo * yHi;
  const z = p + q;
  const zz = p - z + q + xLo * yLo;
  return [z, zz];
}

/** 补偿和（要求 |a| ≥ |b|，vector_norm 的调用序保证）。 */
function dlFastSum(a: number, b: number): [number, number] {
  const x = a + b;
  const y = (a - x) + b;
  return [x, y];
}

function vectorNorm2(axIn: number, ayIn: number): number {
  let ax = axIn;
  let ay = ayIn;
  const max = ax > ay ? ax : ay;
  if (Number.isNaN(max)) return Number.NaN;
  if (Number.isFinite(max) === false) return max;
  if (max === 0.0) return max;
  let outScale = 1.0;
  let maxE = frexpExponent(max);
  if (maxE < -1023) {
    // CPython：subnormal → 两侧除 DBL_MIN 转 normal 后重入，结果乘回。
    ax /= DBL_MIN;
    ay /= DBL_MIN;
    outScale = DBL_MIN;
    maxE = frexpExponent(max / DBL_MIN);
  }
  const scale = Math.pow(2, -maxE); // ldexp(1.0, -max_e)，精确
  let csum = 1.0;
  let frac1 = 0.0;
  let frac2 = 0.0;
  {
    const x1 = ax * scale;
    const pr1 = dlMul(x1, x1);
    const sm1 = dlFastSum(csum, pr1[0]);
    csum = sm1[0];
    frac1 += pr1[1];
    frac2 += sm1[1];
    const x2 = ay * scale;
    const pr2 = dlMul(x2, x2);
    const sm2 = dlFastSum(csum, pr2[0]);
    csum = sm2[0];
    frac1 += pr2[1];
    frac2 += sm2[1];
  }
  let h = Math.sqrt(csum - 1.0 + (frac1 + frac2));
  const pr = dlMul(-h, h);
  const sm = dlFastSum(csum, pr[0]);
  csum = sm[0];
  frac1 += pr[1];
  frac2 += sm[1];
  const x = csum - 1.0 + (frac1 + frac2);
  h += x / (2.0 * h); // sqrt 差分修正
  return (h / scale) * outScale;
}

/**
 * math.hypot(x, y) 的位级孪生（bars 包络半径的半对角等消费）。
 */
export function pythonHypot2(x: number, y: number): number {
  return vectorNorm2(Math.abs(x), Math.abs(y));
}

/**
 * 图符包络半径（px @1x）—— 布局/碰撞/密度用。
 * pie/donut/segmented：外半径；bars：extent box 半对角（Python math.hypot
 * 位级一致 —— 见 pythonHypot2）。
 */
export function glyphBoundRadius(glyph: DiagramGlyph): number {
  const kind = glyph.kind;
  if (
    kind === "composition_pie" || kind === "composition_donut" || kind === "segmented_proportional"
  ) {
    const r = (glyph as CompositionGlyph | SegmentedGlyph).radius_px;
    return typeof r === "number" ? r : 0.0;
  }
  if (kind === "series_bars") {
    const extent = (glyph as SeriesBarsGlyph).extent_px ?? { top: 0.0, bottom: 0.0 };
    const top = Math.abs(typeof extent.top === "number" ? extent.top : 0.0);
    const bottom = Math.abs(typeof extent.bottom === "number" ? extent.bottom : 0.0);
    const segs = (glyph as SeriesBarsGlyph).segments ?? [];
    let halfW = 0.0;
    for (const s of segs) {
      halfW = Math.max(halfW, Math.abs(s.x0), Math.abs(s.x1));
    }
    const halfH = Math.max(top, bottom);
    return pythonHypot2(halfW, halfH);
  }
  throw new DiagramSymbolError("UNKNOWN_KIND", `glyph_bound_radius 不认识 kind=${String(kind)}`);
}

// ── digest ───────────────────────────────────────────────────────────

function compositionToCanonical(g: CompositionGlyph): CanonicalValue {
  const out: Record<string, CanonicalValue> = {
    kind: g.kind,
    radius_px: pyFloat(g.radius_px),
    total: pyFloat(g.total),
    slices: g.slices.map((s): CanonicalValue => ({
      category: s.category,
      value: pyFloat(s.value),
      share: pyFloat(s.share),
      start_angle_deg: pyFloat(s.start_angle_deg),
      end_angle_deg: pyFloat(s.end_angle_deg),
    })),
    zero_slices: pyInt(g.zero_slices),
  };
  if (g.inner_radius_ratio !== undefined) out.inner_radius_ratio = pyFloat(g.inner_radius_ratio);
  if (g.inner_radius_px !== undefined) out.inner_radius_px = pyFloat(g.inner_radius_px);
  if (g.radius_clamped_to_min) out.radius_clamped_to_min = true;
  return out;
}

function seriesBarsToCanonical(g: SeriesBarsGlyph): CanonicalValue {
  return {
    kind: g.kind,
    layout: g.layout,
    baseline_y: pyFloat(g.baseline_y),
    extent_px: { top: pyFloat(g.extent_px.top), bottom: pyFloat(g.extent_px.bottom) },
    segments: g.segments.map((s): CanonicalValue => ({
      category: s.category,
      value: pyFloat(s.value),
      x0: pyFloat(s.x0),
      x1: pyFloat(s.x1),
      y0: pyFloat(s.y0),
      y1: pyFloat(s.y1),
    })),
    zero_values: pyInt(g.zero_values),
  };
}

function segmentedToCanonical(g: SegmentedGlyph): CanonicalValue {
  return {
    kind: g.kind,
    slot_deg: pyFloat(g.slot_deg),
    radius_px: pyFloat(g.radius_px),
    segments: g.segments.map((s): CanonicalValue => ({
      category: s.category,
      value: pyFloat(s.value),
      start_angle_deg: pyFloat(s.start_angle_deg),
      end_angle_deg: pyFloat(s.end_angle_deg),
      radius_px: pyFloat(s.radius_px),
      area_px2: pyFloat(s.area_px2),
    })),
    zero_segments: pyInt(g.zero_segments),
  };
}

/** DiagramGlyph → Python dict 类型保真的 canonical 树（int/float 显式标签）。 */
export function glyphToCanonical(glyph: DiagramGlyph): CanonicalValue {
  if (glyph.kind === "series_bars") return seriesBarsToCanonical(glyph);
  if (glyph.kind === "segmented_proportional") return segmentedToCanonical(glyph);
  return compositionToCanonical(glyph);
}

/** 一批图符几何的确定性摘要（parity 测试与 evidence 共用），与 geometry_digest 对齐。 */
export function geometryDigest(glyphs: readonly DiagramGlyph[]): string {
  return sha256Hex(canonicalJson(glyphs.map(glyphToCanonical)));
}
