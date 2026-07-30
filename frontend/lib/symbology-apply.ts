/**
 * applySymbology - Pure function reducer for symbology template application.
 *
 * Mirrors the backend `apply_template` symbology branch (app/tools/templates.py):
 * both callers MUST emit the same `command` + `params` shape (one seam, two callers).
 *
 *   - single      → LAYER_STYLE_UPDATE with a flat paint style
 *   - categorical → LAYER_STYLE_UPDATE with field + colorMap + baseStyle
 */
export interface SymbologyStyle {
  color?: string;
  fillOpacity?: number;
  opacity?: number;
  strokeColor?: string;
  strokeWidth?: number;
  [key: string]: any;
}

export interface SymbologySinglePayload {
  mode: "single";
  geometry: string;
  style: SymbologyStyle;
}

export interface SymbologyCategoricalPayload {
  mode: "categorical";
  geometry: string;
  // field is injected at apply-time; not part of the preset
  field?: string;
  colorMap: Record<string, string>;
  baseStyle?: SymbologyStyle;
}

export type SymbologyPayload = SymbologySinglePayload | SymbologyCategoricalPayload;

export interface SymbologyApplyResult {
  command: "LAYER_STYLE_UPDATE";
  params: {
    layer_id: string;
    style_applied?: SymbologyStyle;
    field?: string;
    colorMap?: Record<string, string>;
    baseStyle?: SymbologyStyle;
  };
}

/**
 * Apply a symbology payload to produce the LAYER_STYLE_UPDATE dispatch shape.
 * `field` is injected at apply-time for categorical mode (per spec invariant).
 */
export function applySymbology(
  payload: SymbologyPayload,
  layerId: string,
  field?: string
): SymbologyApplyResult {
  if (payload.mode === "categorical") {
    return {
      command: "LAYER_STYLE_UPDATE",
      params: {
        layer_id: layerId,
        field: field ?? payload.field ?? "",
        colorMap: payload.colorMap,
        baseStyle: payload.baseStyle,
      },
    };
  }

  // single mode: normalize the style into the flat paint shape the renderer expects
  const s = payload.style || {};
  const style_applied: SymbologyStyle = {
    color: s.color,
    fill: s.color,
    fillOpacity: s.fillOpacity ?? s.opacity,
    strokeColor: s.strokeColor ?? s.stroke_color,
    strokeWidth: s.strokeWidth ?? s.stroke_width,
  };
  // drop undefined keys so we don't clobber existing renderer values with undefined
  Object.keys(style_applied).forEach((k) => style_applied[k] === undefined && delete style_applied[k]);

  return {
    command: "LAYER_STYLE_UPDATE",
    params: {
      layer_id: layerId,
      style_applied,
    },
  };
}
