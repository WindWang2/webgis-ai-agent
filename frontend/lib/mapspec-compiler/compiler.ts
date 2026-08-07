import {
  MapSpec,
  MapSpecLayer,
  MapSpecCompileResult,
  CompileReport,
  CompileError,
  LegendDef,
  LegendItem,
  StyleMethod,
  SpatialMetaProfile,
} from "./types";
import { generateMapHtml } from "./html-template";

export function isStyleMethodObject(val: any): boolean {
  return (
    val !== null &&
    typeof val === "object" &&
    "method" in val &&
    typeof val.method === "string"
  );
}

export function compileStyleMethod(method: StyleMethod | undefined): any {
  if (method === undefined) return undefined;
  if (!isStyleMethodObject(method)) {
    return method;
  }

  const m = method as any;
  switch (m.method) {
    case "constant":
      return m.value;

    case "field":
      return ["get", m.field];

    case "interpolate": {
      const fieldExpr = ["to-number", ["get", m.field]];
      const flattenedStops: any[] = [];
      for (const [stopVal, outputVal] of m.stops) {
        flattenedStops.push(stopVal, outputVal);
      }
      return ["interpolate", ["linear"], fieldExpr, ...flattenedStops];
    }

    case "step": {
      const fieldExpr = ["to-number", ["get", m.field]];
      const stops = m.stops;
      if (!stops || stops.length === 0) {
        return m.default ?? 0;
      }
      const initialValue = m.default !== undefined ? m.default : stops[0][1];
      const flattenedStops: any[] = [];
      const startIndex = m.default !== undefined ? 0 : 1;
      for (let i = startIndex; i < stops.length; i++) {
        flattenedStops.push(stops[i][0], stops[i][1]);
      }
      return ["step", fieldExpr, initialValue, ...flattenedStops];
    }

    case "match": {
      const fieldExpr = ["get", m.field];
      const cases: any[] = [];
      for (const [caseVal, outputVal] of m.cases) {
        cases.push(caseVal, outputVal);
      }
      const defaultValue = m.default !== undefined ? m.default : cases[cases.length - 1] ?? "";
      return ["match", fieldExpr, ...cases, defaultValue];
    }

    default:
      return m.value ?? undefined;
  }
}

export function validateMapSpec(
  spec: MapSpec,
  profile?: SpatialMetaProfile
): { errors: CompileError[]; warnings: string[] } {
  const errors: CompileError[] = [];
  const warnings: string[] = [];

  if (!spec.sources || Object.keys(spec.sources).length === 0) {
    errors.push({
      code: "MISSING_SOURCES",
      message: "MapSpec has no data sources defined.",
    });
  }

  if (!spec.layers || spec.layers.length === 0) {
    warnings.push("MapSpec has no layers defined.");
  }

  const sourceKeys = new Set(Object.keys(spec.sources || {}));

  for (const layer of spec.layers || []) {
    if (!sourceKeys.has(layer.source)) {
      errors.push({
        code: "INVALID_SOURCE_REF",
        message: `Layer "${layer.id}" references unknown source "${layer.source}".`,
        layerId: layer.id,
      });
    }

    if (layer.paint) {
      for (const [propName, styleMethod] of Object.entries(layer.paint)) {
        if (!styleMethod || !isStyleMethodObject(styleMethod)) continue;
        const m = styleMethod as any;

        if (m.method === "interpolate" || m.method === "step") {
          if (!Array.isArray(m.stops) || m.stops.length < 2) {
            errors.push({
              code: "INVALID_STOPS_COUNT",
              message: `Layer "${layer.id}" property "${propName}" (${m.method}) must have at least 2 stops.`,
              layerId: layer.id,
              field: m.field,
            });
          } else {
            for (let i = 0; i < m.stops.length - 1; i++) {
              if (m.stops[i][0] >= m.stops[i + 1][0]) {
                errors.push({
                  code: "NON_INCREASING_STOPS",
                  message: `Layer "${layer.id}" property "${propName}" (${m.method}) stops must be strictly increasing. Found ${m.stops[i][0]} >= ${m.stops[i + 1][0]}.`,
                  layerId: layer.id,
                  field: m.field,
                });
                break;
              }
            }
          }

          if (profile && profile.fields && m.field && !(m.field in profile.fields)) {
            warnings.push(
              `Layer "${layer.id}" references field "${m.field}" which is not found in spatial profile.`
            );
          }
        } else if (m.method === "match" || m.method === "field") {
          if (profile && profile.fields && m.field && !(m.field in profile.fields)) {
            warnings.push(
              `Layer "${layer.id}" references field "${m.field}" which is not found in spatial profile.`
            );
          }
        }
      }
    }
  }

  return { errors, warnings };
}

/**
 * Convert WGS84 bounds [w, s, e, n] → the 4 corner coordinates a MapLibre
 * `image` source needs, in the order MapLibre expects:
 * [top-left, top-right, bottom-right, bottom-left]. (ADR-0011)
 */
function boundsToImageCorners(bounds: [number, number, number, number]) {
  const [w, s, e, n] = bounds;
  return [
    [w, n], // top-left
    [e, n], // top-right
    [e, s], // bottom-right
    [w, s], // bottom-left
  ];
}

function extractLegendForLayer(layer: MapSpecLayer): LegendDef | null {
  if (!layer.paint) return null;
  const items: LegendItem[] = [];
  const colorProp = layer.paint.color;

  if (colorProp && isStyleMethodObject(colorProp)) {
    const m = colorProp as any;
    if (m.method === "interpolate" || m.method === "step") {
      for (const [val, color] of m.stops) {
        items.push({
          label: `${m.field}: ${val}`,
          color: String(color),
          type: layer.type === "circle" ? "point" : layer.type === "line" ? "line" : "polygon",
        });
      }
    } else if (m.method === "match") {
      for (const [val, color] of m.cases) {
        items.push({
          label: `${m.field}: ${val}`,
          color: String(color),
          type: layer.type === "circle" ? "point" : layer.type === "line" ? "line" : "polygon",
        });
      }
      if (m.default !== undefined) {
        items.push({
          label: "Other",
          color: String(m.default),
          type: layer.type === "circle" ? "point" : layer.type === "line" ? "line" : "polygon",
        });
      }
    } else if (m.method === "constant") {
      items.push({
        label: layer.id,
        color: String(m.value),
        type: layer.type === "circle" ? "point" : layer.type === "line" ? "line" : "polygon",
      });
    }
  } else if (typeof colorProp === "string") {
    items.push({
      label: layer.id,
      color: colorProp,
      type: layer.type === "circle" ? "point" : layer.type === "line" ? "line" : "polygon",
    });
  }

  if (items.length === 0) return null;

  return {
    layerId: layer.id,
    title: layer.id,
    items,
  };
}

export function compileMapSpec(
  spec: MapSpec,
  profile?: SpatialMetaProfile
): MapSpecCompileResult {
  const { errors, warnings } = validateMapSpec(spec, profile);
  const success = errors.length === 0;

  const sources: Record<string, any> = {};
  for (const [key, source] of Object.entries(spec.sources || {})) {
    if (source.type === "raster") {
      // Raster source (ADR-0011) → MapLibre `image` source. The colormap is
      // baked into the PNG at render time; the source carries the image URL +
      // the 4 corner coordinates georeferencing it. The imageRef cursor is
      // emitted verbatim as the url — a session-aware rewrite step (in the
      // compile caller, which has session_id) turns `ref:raster/<id>` into the
      // serving route. The compiler stays session-agnostic by design.
      sources[key] = {
        type: "image",
        url: source.imageRef,
        coordinates: boundsToImageCorners(source.bounds),
      };
    } else if (source.type === "geojson" && source.inlineData) {
      sources[key] = {
        type: "geojson",
        data: source.inlineData,
      };
    } else if (source.type === "geojson" && (source.url || source.dataPath)) {
      sources[key] = {
        type: "geojson",
        data: source.url || source.dataPath,
      };
    } else {
      // vector 源仅由 runtime adapter 发射（Data Plane 瓦片路径），静态编译
      // 路径用空 geojson 占位。
      sources[key] = {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      };
    }
  }

  const compiledLayers: any[] = [];
  const legends: LegendDef[] = [];
  let labelLayerCount = 0;

  for (const layer of spec.layers || []) {
    const layerType = layer.type;
    const maplibreLayer: any = {
      id: layer.id,
      type: layerType,
      source: layer.source,
      layout: {},
      paint: {},
    };

    if (layer.layout?.visibility) {
      maplibreLayer.layout.visibility = layer.layout.visibility;
    }

    if (layer.paint) {
      if (layerType === "circle") {
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["circle-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.radius !== undefined)
          maplibreLayer.paint["circle-radius"] = compileStyleMethod(layer.paint.radius);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["circle-opacity"] = compileStyleMethod(layer.paint.opacity);
        if (layer.paint.strokeColor !== undefined)
          maplibreLayer.paint["circle-stroke-color"] = compileStyleMethod(layer.paint.strokeColor);
        if (layer.paint.strokeWidth !== undefined)
          maplibreLayer.paint["circle-stroke-width"] = compileStyleMethod(layer.paint.strokeWidth);
      } else if (layerType === "line") {
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["line-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.width !== undefined)
          maplibreLayer.paint["line-width"] = compileStyleMethod(layer.paint.width);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["line-opacity"] = compileStyleMethod(layer.paint.opacity);
      } else if (layerType === "fill") {
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["fill-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["fill-opacity"] = compileStyleMethod(layer.paint.opacity);
        if (layer.paint.strokeColor !== undefined)
          maplibreLayer.paint["fill-outline-color"] = compileStyleMethod(layer.paint.strokeColor);
      } else if (layerType === "heatmap") {
        if (layer.paint.radius !== undefined)
          maplibreLayer.paint["heatmap-radius"] = compileStyleMethod(layer.paint.radius);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["heatmap-opacity"] = compileStyleMethod(layer.paint.opacity);
      } else if (layerType === "raster") {
        // Raster layer (ADR-0011): colors are baked into the source image; the
        // only paint property is opacity. A raster layer references its
        // (already-emitted) `image` source by id.
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["raster-opacity"] = compileStyleMethod(layer.paint.opacity);
      }
    }

    compiledLayers.push(maplibreLayer);

    const legend = extractLegendForLayer(layer);
    if (legend) {
      legends.push(legend);
    }

    const labelSpec = layer.label || (layer.layout?.labelField ? { field: layer.layout.labelField } : undefined);
    if (labelSpec && labelSpec.field) {
      labelLayerCount++;
      const labelLayer: any = {
        id: `${layer.id}-label`,
        type: "symbol",
        source: layer.source,
        layout: {
          "text-field": ["get", labelSpec.field],
          "text-size": compileStyleMethod(labelSpec.size ?? layer.layout?.labelSize ?? 12),
          "text-allow-overlap": false,
        },
        paint: {
          "text-color": compileStyleMethod(labelSpec.color ?? layer.layout?.labelColor ?? "#000000"),
        },
      };

      if (labelSpec.haloColor) {
        labelLayer.paint["text-halo-color"] = labelSpec.haloColor;
        labelLayer.paint["text-halo-width"] = labelSpec.haloWidth ?? 1;
      }

      compiledLayers.push(labelLayer);
    }
  }

  const center = spec.view?.center ?? [0, 0];
  const zoom = spec.view?.zoom ?? 2;

  const style = {
    version: 8,
    name: "MapSpec Compiled Style",
    center,
    zoom,
    bearing: spec.view?.bearing ?? 0,
    pitch: spec.view?.pitch ?? 0,
    sources,
    layers: compiledLayers,
  };

  const report: CompileReport = {
    success,
    errors,
    warnings,
    stats: {
      sourceCount: Object.keys(spec.sources || {}).length,
      layerCount: (spec.layers || []).length,
      compiledLayerCount: compiledLayers.length,
      labelLayerCount,
    },
  };

  const html = generateMapHtml(style, spec.layout);

  return {
    style,
    html,
    legend: legends,
    report,
  };
}

export class MapSpecCompilerEngine {
  public static compile(spec: MapSpec, profile?: SpatialMetaProfile): MapSpecCompileResult {
    return compileMapSpec(spec, profile);
  }

  public static validate(spec: MapSpec, profile?: SpatialMetaProfile): { errors: CompileError[]; warnings: string[] } {
    return validateMapSpec(spec, profile);
  }
}
