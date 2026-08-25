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

/**
 * #1007：style 级 glyphs 模板进配置。缺省保留公共 demotiles 源；内网/
 * 离线部署通过 NEXT_PUBLIC_MAP_GLYPHS_URL 指向本地字形托管（如
 * https://intranet/fonts/{fontstack}/{range}.pbf），Canvas/SVG 导出的标注
 * 层不再因外部字体源不可达而缺字。
 */
export const MAP_GLYPHS_URL =
  process.env.NEXT_PUBLIC_MAP_GLYPHS_URL ??
  "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf";

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
      const stops = m.stops;
      if (!Array.isArray(stops) || stops.length === 0) {
        return m.default ?? 0;
      }
      const flattenedStops: any[] = [];
      for (const [stopVal, outputVal] of stops) {
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
      const rawCases = m.cases;
      if (!Array.isArray(rawCases) || rawCases.length === 0) {
        return m.default ?? "";
      }
      const cases: any[] = [];
      for (const [caseVal, outputVal] of rawCases) {
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
      for (const [val, color] of m.stops ?? []) {
        items.push({
          label: `${m.field}: ${val}`,
          color: String(color),
          type: layer.type === "circle" ? "point" : layer.type === "line" ? "line" : "polygon",
        });
      }
    } else if (m.method === "match") {
      for (const [val, color] of m.cases ?? []) {
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
    } else if ((source as any).type === "vector") {
      const v = source as any;
      sources[key] = {
        type: "vector",
        tiles: v.tiles,
        minzoom: v.minzoom ?? 0,
        maxzoom: v.maxzoom ?? 14,
      };
    } else {
      // Genuinely unknown source types fall back to an empty GeoJSON placeholder.
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
    // Vector source layers require `source-layer` in MapLibre. MapSpecLayer
    // carries an optional `sourceLayer` passthrough; default to "data" (the
    // encoder's layer name in mvt.py).
    const srcDef: any = (spec.sources as any)?.[layer.source];
    if (srcDef?.type === "vector") {
      maplibreLayer["source-layer"] = (layer as any).sourceLayer ?? "data";
    }

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
        // heatmap-color 的输入必须是 heatmap-density，MapSpec 的高级 paint 方法
        // （interpolate 等按要素属性取值）表达不了。约定 paint.color 传常量热色
        // （raw hex 字符串），编译器生成 transparent→hot 的密度 ramp；缺省不动，
        // 保持 MapLibre 默认 ramp —— 测试需求不改生产默认值。
        const hotColor = layer.paint.color;
        if (typeof hotColor === "string") {
          // 0.1 的密度阈值：让低密度区域也映到热色（孤立点场景可判定），
          // 0 处保持全透明。
          maplibreLayer.paint["heatmap-color"] = [
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
        // intensity/weight 同理可选显式覆盖（默认仍是 MapLibre 的 1）。
        if (layer.paint.intensity !== undefined)
          maplibreLayer.paint["heatmap-intensity"] = compileStyleMethod(layer.paint.intensity);
        if (layer.paint.weight !== undefined)
          maplibreLayer.paint["heatmap-weight"] = compileStyleMethod(layer.paint.weight);
        // 方言桥接：dispatch 授权链路（analysis_cartography_converter）产出
        // 的 heatmap 层携带的是 MapLibre 原生 heatmap-* paint 表达式（含
        // zoom 插值 radius 与密度色带）。live runtime 直传它们；headless
        // 编译此前只认高级键 → 授权层编译出空 paint。此处显式透传已知的
        // 原生键（优先级低于上面的高级键），两套方言不再漂移。
        for (const rawKey of [
          "heatmap-weight",
          "heatmap-intensity",
          "heatmap-color",
          "heatmap-radius",
          "heatmap-opacity",
        ] as const) {
          const rawValue = (layer.paint as Record<string, unknown>)[rawKey];
          if (rawValue !== undefined && maplibreLayer.paint[rawKey] === undefined) {
            maplibreLayer.paint[rawKey] = rawValue as unknown as StyleMethod;
          }
        }
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
          // #1007：默认 1px 白色 halo（GIS 标注惯例）。编译器在 lib 层没有
          // 主题上下文——主题令牌是 CSS 变量，MapLibre style 与 Canvas/SVG
          // 导出都解析不了，无法让默认色随主题；裸黑字在暗色底图上不可读
          // （导出图同病）。黑字 + 白晕在任意底图上保持可读（与
          // map-commands/annotationHelpers 的在制标注同款），显式
          // haloColor/haloWidth 完全尊重。SVG 导出读同一组 paint 键，自动受益。
          "text-halo-color": labelSpec.haloColor ?? "#ffffff",
          "text-halo-width": labelSpec.haloWidth ?? 1,
        },
      };
      if (srcDef?.type === "vector") {
        labelLayer["source-layer"] = (layer as any).sourceLayer ?? "data";
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
  if (labelLayerCount > 0) {
    // symbol 图层的 text-field 在 MapLibre 里要求 style 级 glyphs 模板，
    // 否则运行时报错（symbol-label 场景暴露的真实编译缺陷）。
    // #1007：URL 进配置（NEXT_PUBLIC_MAP_GLYPHS_URL），支持本地字形托管。
    (style as Record<string, unknown>).glyphs = MAP_GLYPHS_URL;
  }

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
