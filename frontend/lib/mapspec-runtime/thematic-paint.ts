import type { LegendSpec } from "@/lib/map-kit/types";

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

  if (type === "graduated") return graduatedToStep(spec as any);
  if (type === "continuous" || type === "divergent") return domainToInterpolate(spec as any);
  if (type === "categorical") return categoricalToMatch(spec as any);

  return null;
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
  return withNoDataGuard(field, thematic, spec.nodata);
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
  return withNoDataGuard(field, thematic, spec.nodata);
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
