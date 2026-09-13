import type { LegendSpec } from "@/lib/map-kit/types";
import { recordSymbolLawEvidence } from "@/lib/map-kit/symbol-law";

/**
 * Derive a MapLibre data-driven color expression from a canonical
 * {@link LegendSpec} (ADR-0078).
 *
 * This is the frontend mirror of the backend
 * `app/lib/cartography/thematic_spec.py::spec_to_paint`. Both are deterministic
 * projections of the SAME `legend_spec`, so the live map's paint and the legend
 * overlay cannot diverge — they read one source.
 *
 * The MapSpecRuntime applies a layer's `paint` dict straight to MapLibre
 * (`runtime.ts::addLayerSafe` → `map.addLayer({paint})`), so unlike the headless
 * compiler path (which lowers a `StyleMethod` via `compileStyleMethod`) the live
 * adapter must emit **MapLibre-native** expressions directly. For step /
 * interpolate / match the expression shapes match `compileStyleMethod`'s output,
 * so live and headless agree — EXCEPT the optional no-data guard
 * (`withNoDataGuard`, below) is applied only on the live path when
 * `legend_spec.nodata` is set; the headless `spec_to_paint` does not encode it,
 * so a thematic spec with a `nodata` rule renders slightly differently live vs
 * export (live diverts nulls to the no-data color; export coerces them into the
 * lowest class).
 *
 * Returns `null` when `legend_spec` is absent/invalid → the caller keeps the
 * legacy flat-color path (`fill_color` property coalesce + `style.color`
 * fallback), preserving backward compatibility for non-thematic layers
 * (`apply_layer_style`, plain single-color results).
 *
 * No-data semantics: for numeric encodings (step/interpolate) a null/missing
 * field value would otherwise be coerced by `to-number` to `0` and silently
 * land in the LOWEST class. When `legend_spec.nodata` is present the expression
 * is wrapped so null/missing values take the no-data color instead. Categorical
 * (`match`) needs no guard — its `default` arm already absorbs unmatched/null
 * values.
 *
 * AC-06 (ADR-0155) P6 — legend_spec v2 (ADR-0152, additive freeze) 投影：
 *  - `out_of_range`（clip_p99 实际裁剪时存在）：值 > upper 的要素显式映射到
 *    `out_of_range.color`（case 包装），裁剪尾不再静默落入顶类；
 *  - `unit` / `k` / `method` / `palette_id` / `why` / `nodata_label` /
 *    `out_of_range_label` / `context`：图例/裁决元数据，不映射 paint ——
 *    记入 evidence（不静默丢弃），07 线图例渲染消费同一批字段；
 *  - `clip_policy`: none/clip_p99/head_tail 由 out_of_range 语义承载；log
 *    为后端分类前变换（breaks 已是原域），paint 侧无需再变换（evidence 披露）。
 */

/** True when a legend_spec carries a usable thematic encoding.
 * Aligned with the backend `is_thematic` contract: a known mode AND a non-empty
 * field. (A data-driven live-map expression requires a field; a spec without one
 * yields a null expression and the flat-color fallback.) */
const THEMATIC_MODES = new Set(["graduated", "continuous", "categorical", "divergent"]);
export function isThematic(spec: LegendSpec | null | undefined): spec is LegendSpec {
  return (
    !!spec &&
    typeof spec === "object" &&
    THEMATIC_MODES.has((spec as any).type) &&
    typeof (spec as any).field === "string" &&
    (spec as any).field.length > 0
  );
}

/** The single thematic field identity for filter/paint/legend consistency. */
export function thematicField(spec: LegendSpec | null | undefined): string | null {
  if (!spec) return null;
  const f = (spec as any).field;
  return typeof f === "string" && f.length > 0 ? f : null;
}

/**
 * Build a MapLibre color expression from a legend_spec, or `null` if the spec
 * does not yield a data-driven thematic expression (caller falls back to flat
 * color). Pure: no React/MapLibre instances, O(classes) work only.
 */
export function legendSpecToColorExpression(
  spec: LegendSpec | null | undefined,
): unknown | null {
  if (!spec || typeof spec !== "object") return null;
  const type = (spec as any).type as string;

  // AC-06 P6：v2 图例/裁决元数据披露（unit/k/method/… —— paint 不消费，
  // 但被显式记账，图例渲染从同一 spec 读取）。
  discloseLegendV2Metadata(spec as unknown as Record<string, unknown>);

  if (type === "graduated") return graduatedToStep(spec as any);
  if (type === "continuous" || type === "divergent") return domainToInterpolate(spec as any);
  if (type === "categorical") return categoricalToMatch(spec as any);

  return null;
}

// ─── AC-06 P6：legend_spec v2 投影辅助 ──────────────────────────────────────

/** v2 中仅图例/裁决语义、不映射 paint 的字段（消费方 = 07 线图例渲染）。 */
const V2_METADATA_KEYS = [
  "unit",
  "k",
  "method",
  "palette_id",
  "why",
  "nodata_label",
  "out_of_range_label",
  "context",
] as const;

/**
 * v2 元数据披露：出现在 spec 上的图例/裁决字段记一条 evidence —— 上游可
 * 观测到它们被前端接收（不静默丢弃），图例渲染（07 线）从同一 spec 读取。
 */
export function discloseLegendV2Metadata(
  spec: Record<string, unknown>,
  layerId?: string,
): void {
  const present = V2_METADATA_KEYS.filter((k) => spec[k] !== undefined);
  const clipPolicy = spec.clip_policy;
  if (present.length === 0 && clipPolicy === undefined) return;
  recordSymbolLawEvidence(
    "legend-v2-metadata",
    {
      fields: present,
      clip_policy: clipPolicy ?? "none",
      // log 策略：breaks 已是后端分类前变换后的原域值，paint 侧无需再变换。
      paint_transform: clipPolicy === "log" ? "none (breaks already in original domain)" : "none",
    },
    layerId,
  );
}

/**
 * v2 out_of_range guard：值 > upper 的裁剪尾显式映射到 `out_of_range.color`
 * （不再是顶类静默承接）。包在 nodata guard 之内（null 先行短路）。
 */
function withOutOfRangeGuard(
  field: string,
  thematic: unknown[],
  outOfRange: any,
): unknown[] {
  if (!outOfRange || typeof outOfRange !== "object") return thematic;
  const upper = Number(outOfRange.upper);
  const color = outOfRange.color;
  if (!Number.isFinite(upper) || typeof color !== "string") return thematic;
  return [
    "case",
    [">", ["to-number", ["get", field]], upper],
    color,
    thematic,
  ];
}

// ─── projections (mirror backend thematic_spec._*_to_*) ─────────────────────

function graduatedToStep(spec: any): unknown | null {
  const field: string | undefined = spec.field;
  const breaks: unknown[] = Array.isArray(spec.breaks) ? spec.breaks : [];
  const colors: unknown[] = Array.isArray(spec.palette_colors)
    ? spec.palette_colors
    : Array.isArray(spec.colors)
      ? spec.colors
      : [];

  const numericBreaks = breaks.filter(isFiniteNumber);
  if (!field || numericBreaks.length < 2 || colors.length < 1) return null;

  // Backend contract: default = palette_colors[0]; stops start at breaks[1].
  const defaultValue = colors[0];
  const stops: unknown[] = [];
  for (let i = 1; i < numericBreaks.length - 1; i++) {
    const color = i < colors.length ? colors[i] : colors[colors.length - 1];
    stops.push(Number(numericBreaks[i]), color);
  }
  const thematic = ["step", ["to-number", ["get", field]], defaultValue, ...stops];
  // AC-06 P6：裁剪尾 guard（out_of_range）在 nodata guard 之内。
  return withNoDataGuard(
    field,
    withOutOfRangeGuard(field, thematic, spec.out_of_range),
    spec.nodata,
  );
}

function domainToInterpolate(spec: any): unknown | null {
  const field: string | undefined = spec.field;
  const min = spec.min;
  const max = spec.max;
  const colors: unknown[] = Array.isArray(spec.palette_colors)
    ? spec.palette_colors
    : Array.isArray(spec.colors)
      ? spec.colors
      : [];

  if (
    !field ||
    !isFiniteNumber(min) ||
    !isFiniteNumber(max) ||
    !(Number(min) < Number(max)) ||
    colors.length < 2
  ) {
    return null;
  }

  const n = colors.length;
  const step = (Number(max) - Number(min)) / (n - 1);
  const stops: unknown[] = [];
  for (let i = 0; i < n; i++) {
    const stopVal = round6(Number(min) + i * step);
    stops.push(stopVal, colors[i]);
  }
  const thematic = ["interpolate", ["linear"], ["to-number", ["get", field]], ...stops];
  // AC-06 P6：裁剪尾 guard（out_of_range）在 nodata guard 之内。
  return withNoDataGuard(
    field,
    withOutOfRangeGuard(field, thematic, spec.out_of_range),
    spec.nodata,
  );
}

function categoricalToMatch(spec: any): unknown | null {
  const field: string | undefined = spec.field;
  const categories: any[] = Array.isArray(spec.categories) ? spec.categories : [];
  if (!field || categories.length < 1) return null;

  const cases: unknown[] = [];
  for (const cat of categories) {
    if (cat && typeof cat === "object" && cat.key != null && cat.color) {
      cases.push(cat.key, cat.color);
    } else if (Array.isArray(cat) && cat.length >= 2) {
      cases.push(cat[0], cat[1]);
    }
  }
  if (cases.length === 0) return null;

  // Backend contract: default = last category color (legend_spec.default ignored).
  const lastColor = cases[cases.length - 1];
  // AC-06 P6：out_of_range 是数值域语义，categorical（match 吸收全部未命中）
  // 不适用 —— 显式 evidence 而非静默忽略。
  if (spec.out_of_range !== undefined) {
    recordSymbolLawEvidence("unmapped-paint-key", {
      key: "out_of_range",
      native: "categorical match expression",
      reason: "out_of_range applies to numeric encodings; categorical default arm already absorbs out-of-domain values",
    });
  }
  return ["match", ["get", field], ...cases, lastColor];
}

/**
 * Wrap a numeric thematic expression so a null/missing field value is diverted
 * to the no-data color instead of being coerced by `to-number` into the lowest
 * class. `["get", field]` returns null for both null-valued and absent
 * properties in MapLibre, so a single `== null` test covers both. No-op when
 * no `nodata` rule is declared (backward compat for legacy legend_specs).
 */
function withNoDataGuard(field: string, thematic: unknown[], nodata: any): unknown {
  if (!nodata || typeof nodata !== "object" || !nodata.color) return thematic;
  return ["case", ["==", ["get", field], null], nodata.color, thematic];
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function round6(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}
