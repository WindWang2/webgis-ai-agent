export interface ThematicChoroplethPayload {
  variant: "choropleth";
  method: "quantiles" | "equal_interval" | "natural_breaks" | "lisa";
  k: number;
  palette: string;
}

export interface ThematicHeatmapPayload {
  variant: "heatmap";
  intensity?: number;
  radius?: number;
  heatPalette?: string[];
}

export type ThematicPresetPayload = ThematicChoroplethPayload | ThematicHeatmapPayload;

export interface ThematicResolveResult {
  variant: "choropleth" | "heatmap";
  toolCall: {
    tool: string;
    params: Record<string, any>;
  };
  legendSpec: {
    type: "graduated" | "continuous" | "categorical";
    field: string;
    palette?: string;
    palette_colors?: string[];
    heatPalette?: string[];
  };
}

/**
 * resolveThematicPreset - Pure function reducer for thematic map preset application.
 *
 * Dispatches by variant (choropleth vs heatmap) and injects apply-time field.
 */
export function resolveThematicPreset(
  payload: ThematicPresetPayload,
  field: string
): ThematicResolveResult {
  if (payload.variant === "heatmap") {
    const heatPalette = payload.heatPalette || ["#0000ff", "#00ff00", "#ffff00", "#ff0000"];
    return {
      variant: "heatmap",
      toolCall: {
        tool: "add_native_heatmap",
        params: {
          field,
          intensity: payload.intensity ?? 0.8,
          radius: payload.radius ?? 25,
          heatPalette,
        },
      },
      legendSpec: {
        type: "continuous",
        field,
        heatPalette,
      },
    };
  }

  // Choropleth variant (quantiles / equal_interval / natural_breaks / lisa)
  const isLisa = payload.method === "lisa";
  return {
    variant: "choropleth",
    toolCall: {
      tool: "create_thematic_map",
      params: {
        field,
        method: payload.method || "quantiles",
        k: payload.k || 5,
        palette: payload.palette || "YlOrRd",
      },
    },
    legendSpec: {
      type: isLisa ? "categorical" : "graduated",
      field,
      palette: payload.palette || "YlOrRd",
    },
  };
}
