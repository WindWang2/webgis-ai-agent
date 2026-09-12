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
// AC-06 (ADR-0155)：符号律 + 密度自适应 + evidence。
import {
  circleRadiusExpression,
  heatmapRadiusExpression,
  lineWidthExpression,
  opacityForCount,
  recordSymbolLawEvidence,
  resolveDensityPresentation,
  type DensityPresentation,
} from "@/lib/map-kit/symbol-law";

/**
 * AC-06：headless 编译器可识别的源类型白名单。白名单外的类型此前静默降级
 * 为空 FeatureCollection（图层看似成功、实际无数据 —— 诊断黑洞）；现在
 * validateMapSpec 产 UNKNOWN_SOURCE_TYPE 编译错误 + evidence，编译产物
 * 不再输出占位空源。
 */
const KNOWN_SOURCE_TYPES = new Set(["raster", "geojson", "vector", "raster-dem"]);

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
      // AC-06 (ADR-0155)：插值模式补齐 —— exponential / cubic-bezier；
      // 缺省 linear 与既有产出逐字节等价。field === "zoom" 走相机插值
      // （不带 to-number / get 包装 —— MapLibre zoom 是表达式原语）。
      const fieldExpr: unknown[] =
        m.field === "zoom" ? ["zoom"] : ["to-number", ["get", m.field]];
      const interp = m.interpolation;
      const interpolationExpr: unknown[] = (() => {
        if (!interp || interp.kind === "linear") return ["linear"];
        if (interp.kind === "exponential") {
          const base = Number(interp.base);
          return ["exponential", Number.isFinite(base) && base > 0 ? base : 1];
        }
        if (interp.kind === "cubic-bezier" && Array.isArray(interp.controlPoints)) {
          const [x1, y1, x2, y2] = interp.controlPoints.map(Number);
          if ([x1, y1, x2, y2].every(Number.isFinite)) {
            return ["cubic-bezier", x1, y1, x2, y2];
          }
        }
        return ["linear"];
      })();
      const stops = m.stops;
      if (!Array.isArray(stops) || stops.length === 0) {
        return m.default ?? 0;
      }
      const flattenedStops: any[] = [];
      for (const [stopVal, outputVal] of stops) {
        flattenedStops.push(stopVal, outputVal);
      }
      return ["interpolate", interpolationExpr, fieldExpr, ...flattenedStops];
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

  // AC-06：未知源类型显式报错（此前静默降级空 FeatureCollection）。
  // raster/geojson/vector/raster-dem 之外（含 data_fabric/wms/wmts/pmtiles
  // —— 它们没有可编译的数据面语义）一律 UNKNOWN_SOURCE_TYPE。
  for (const [sourceId, source] of Object.entries(spec.sources || {})) {
    if (!KNOWN_SOURCE_TYPES.has((source as any).type)) {
      errors.push({
        code: "UNKNOWN_SOURCE_TYPE",
        message: `Source "${sourceId}" has unknown type "${(source as any).type}". Headless compilation refuses to emit a silent empty placeholder.`,
      });
      recordSymbolLawEvidence("unknown-source-type", { sourceType: (source as any).type }, sourceId);
    }
  }

  for (const layer of spec.layers || []) {
    // AC-06：background 层无数据面（source 携带 "" 哨兵），跳过源引用检查。
    if (layer.type !== "background" && !sourceKeys.has(layer.source)) {
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
  const colorProp = layer.paint.color ?? (layer.paint as any)["fill-extrusion-color"];

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

  // ── AC-06 P2：密度自适应表达切换（预扫描）───────────────────────────
  // 点层按 inlineData 要素数裁决 native/cluster/heatmap；cluster 自动落到
  // geojson 源配置（复用既有 __clusters 子层范式）；heatmap 改写编译层型。
  // 阈值以 VIEWPORT_RENDER_BUDGET=5000 / MVT 5000 为基准（symbol-law）。
  const autoClusterSourceIds = new Set<string>();
  const presentationByLayerId = new Map<string, DensityPresentation>();
  for (const layer of spec.layers || []) {
    if (layer.type !== "circle") continue;
    const srcDef = (spec.sources as any)?.[layer.source];
    if (srcDef?.type !== "geojson") continue;
    const features = srcDef?.inlineData?.features;
    const count = Array.isArray(features) ? features.length : Number.NaN;
    if (!Number.isFinite(count)) continue;
    const presentation = resolveDensityPresentation({
      geometryType: "Point",
      featureCount: count,
      explicitCluster: !!(srcDef?.cluster || layer.cluster),
    });
    presentationByLayerId.set(layer.id, presentation);
    if (presentation.mode === "cluster") {
      autoClusterSourceIds.add(layer.source);
      recordSymbolLawEvidence("density-switch", { from: "native", to: "cluster", featureCount: count }, layer.id);
    } else if (presentation.mode === "heatmap") {
      recordSymbolLawEvidence("density-switch", { from: "native", to: "heatmap", featureCount: count }, layer.id);
    } else {
      recordSymbolLawEvidence("presentation-decision", { mode: "native", featureCount: count }, layer.id);
    }
  }

  const sources: Record<string, any> = {};
  for (const [key, source] of Object.entries(spec.sources || {})) {
    if (!KNOWN_SOURCE_TYPES.has(source.type)) {
      // AC-06：未知类型不再静默产出空 FeatureCollection 占位 —— 错误已在
      // validateMapSpec 报告（UNKNOWN_SOURCE_TYPE），这里直接跳过。
      continue;
    }
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
      // AC-06 P2：密度自适应自动聚合 —— 裁决为 cluster 的层所引用的源补
      // 聚合配置（显式 cluster 配置优先，符号律只兜缺省）。
      const autoCluster = autoClusterSourceIds.has(key) && !source.cluster;
      sources[key] = {
        type: "geojson",
        data: source.inlineData,
        ...(source.cluster || autoCluster
          ? {
              cluster: true,
              clusterRadius: source.cluster?.radius ?? 60,
              clusterMaxZoom: source.cluster?.maxzoom ?? 14,
            }
          : {}),
      };
    } else if (source.type === "geojson" && (source.url || source.dataPath)) {
      sources[key] = {
        type: "geojson",
        data: source.url || source.dataPath,
        ...(source.cluster ? { cluster: true, clusterRadius: source.cluster.radius ?? 60, clusterMaxZoom: source.cluster.maxzoom ?? 14 } : {}),
      };
    } else if ((source as any).type === "vector") {
      const v = source as any;
      sources[key] = {
        type: "vector",
        tiles: v.tiles,
        minzoom: v.minzoom ?? 0,
        maxzoom: v.maxzoom ?? 14,
      };
    } else if (source.type === "raster-dem") {
      // AC-06 (ADR-0155)：hillshade 的数据面（raster-dem 源最小投影）。
      const d = source as any;
      sources[key] = {
        type: "raster-dem",
        url: d.url,
        ...(d.tileSize !== undefined ? { tileSize: d.tileSize } : {}),
        ...(d.encoding !== undefined ? { encoding: d.encoding } : {}),
      };
    } else {
      // geojson 无 inlineData 且无 url/dataPath：沿用空 FeatureCollection
      // （schema 合法的空源），但真正未知类型永远到不了这里（上方已跳过）。
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
    const srcDef: any = (spec.sources as any)?.[layer.source];
    const features = srcDef?.type === "geojson" ? srcDef?.inlineData?.features : undefined;
    const featureCount: number | undefined = Array.isArray(features) ? features.length : undefined;
    // AC-06 P2：密度裁决为 heatmap 的 circle 层按 heatmap 编译（点数超出
    // 预算时逐点符号已无读性；spec 契约层型不变，只改写编译产物）。
    const presentation = presentationByLayerId.get(layer.id);
    const layerType =
      layer.type === "circle" && presentation?.mode === "heatmap" ? "heatmap" : layer.type;
    const maplibreLayer: any = {
      id: layer.id,
      type: layerType,
      // AC-06：background 层无数据面 —— 省略 source 键（MapLibre 契约）。
      ...(layer.type === "background" ? {} : { source: layer.source }),
      layout: {},
      paint: {},
    };
    // Vector source layers require `source-layer` in MapLibre. MapSpecLayer
    // carries an optional `sourceLayer` passthrough; default to "data" (the
    // encoder's layer name in mvt.py).
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
        // AC-06 P4：blur / translate 表达力（此前无通道）。
        if (layer.paint.blur !== undefined)
          maplibreLayer.paint["circle-blur"] = compileStyleMethod(layer.paint.blur);
        if (layer.paint.translate !== undefined)
          maplibreLayer.paint["circle-translate"] = compileStyleMethod(layer.paint.translate);
        if (layer.paint.translateAnchor !== undefined)
          maplibreLayer.paint["circle-translate-anchor"] = compileStyleMethod(layer.paint.translateAnchor);
      } else if (layerType === "line") {
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["line-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.width !== undefined)
          maplibreLayer.paint["line-width"] = compileStyleMethod(layer.paint.width);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["line-opacity"] = compileStyleMethod(layer.paint.opacity);
        // AC-06 P4：dash-array / blur / translate 表达力。
        if (layer.paint.dashArray !== undefined)
          maplibreLayer.paint["line-dasharray"] = compileStyleMethod(layer.paint.dashArray);
        if (layer.paint.blur !== undefined)
          maplibreLayer.paint["line-blur"] = compileStyleMethod(layer.paint.blur);
        if (layer.paint.translate !== undefined)
          maplibreLayer.paint["line-translate"] = compileStyleMethod(layer.paint.translate);
        if (layer.paint.translateAnchor !== undefined)
          maplibreLayer.paint["line-translate-anchor"] = compileStyleMethod(layer.paint.translateAnchor);
      } else if (layerType === "fill") {
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["fill-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["fill-opacity"] = compileStyleMethod(layer.paint.opacity);
        if (layer.paint.strokeColor !== undefined)
          maplibreLayer.paint["fill-outline-color"] = compileStyleMethod(layer.paint.strokeColor);
        // AC-06 P4：fill translate；outlineWidth 是 MapLibre 契约不存在的
        // 属性 —— 显式 evidence（不静默丢弃），便于上游修正表达。
        if (layer.paint.translate !== undefined)
          maplibreLayer.paint["fill-translate"] = compileStyleMethod(layer.paint.translate);
        if (layer.paint.translateAnchor !== undefined)
          maplibreLayer.paint["fill-translate-anchor"] = compileStyleMethod(layer.paint.translateAnchor);
        if (layer.paint.outlineWidth !== undefined) {
          recordSymbolLawEvidence("unmapped-paint-key", {
            key: "outlineWidth",
            native: "fill-outline-width",
            reason: "MapLibre style spec has no fill-outline-width property",
          }, layer.id);
        }
      } else if (layerType === "symbol") {
        // AC-06 P4：symbol 层此前静默空 paint（白名单缺口）。canonical 语义：
        // color/opacity/strokeColor/strokeWidth 投影到 text-*（symbol 默认
        // 语义是标注）；icon-*/text-* 原生键透传不覆盖。
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["text-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["text-opacity"] = compileStyleMethod(layer.paint.opacity);
        if (layer.paint.strokeColor !== undefined)
          maplibreLayer.paint["text-halo-color"] = compileStyleMethod(layer.paint.strokeColor);
        if (layer.paint.strokeWidth !== undefined)
          maplibreLayer.paint["text-halo-width"] = compileStyleMethod(layer.paint.strokeWidth);
        for (const [rawKey, rawValue] of Object.entries(layer.paint as Record<string, unknown>)) {
          if (rawKey === "color" || rawKey === "opacity" || rawKey === "strokeColor" || rawKey === "strokeWidth") continue;
          if (!(rawKey.startsWith("icon-") || rawKey.startsWith("text-"))) continue;
          if (rawValue !== undefined && maplibreLayer.paint[rawKey] === undefined) {
            maplibreLayer.paint[rawKey] = isStyleMethodObject(rawValue)
              ? compileStyleMethod(rawValue as StyleMethod)
              : rawValue;
          }
        }
      } else if (layerType === "background") {
        // AC-06 P4：background 基础支持（无源层；color/opacity 两个 paint 面）。
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["background-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["background-opacity"] = compileStyleMethod(layer.paint.opacity);
      } else if (layerType === "hillshade") {
        // AC-06 P4：hillshade 基础支持（消费 raster-dem 源）。canonical
        // opacity 语义 = 渲染强度（exaggeration）；hillshade-* 原生键直通。
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["hillshade-exaggeration"] = compileStyleMethod(layer.paint.opacity);
        for (const [rawKey, rawValue] of Object.entries(layer.paint as Record<string, unknown>)) {
          if (!rawKey.startsWith("hillshade-")) continue;
          if (rawValue !== undefined && maplibreLayer.paint[rawKey] === undefined) {
            maplibreLayer.paint[rawKey] = isStyleMethodObject(rawValue)
              ? compileStyleMethod(rawValue as StyleMethod)
              : rawValue;
          }
        }
      } else if (layerType === "heatmap") {
        if (layer.paint.radius !== undefined)
          maplibreLayer.paint["heatmap-radius"] = compileStyleMethod(layer.paint.radius);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["heatmap-opacity"] = compileStyleMethod(layer.paint.opacity);
        // heatmap-color 的输入必须是 heatmap-density，MapSpec 的高级 paint 方法
        // （interpolate 等按要素属性取值）表达不了。约定 paint.color 传常量热色
        // （raw hex 字符串），编译器生成 transparent→hot 的密度 ramp；缺省不动，
        // 保持 MapLibre 默认 ramp —— 测试需求不改生产默认值。
        const rawColor = layer.paint.color as any;
        // AC-06：常量 hex 的两种合法形态 —— 裸字符串与 constant 方法对象。
        const hotColor =
          typeof rawColor === "string"
            ? rawColor
            : rawColor?.method === "constant" && typeof rawColor.value === "string"
              ? rawColor.value
              : undefined;
        if (hotColor !== undefined) {
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
        } else if (rawColor !== undefined) {
          // AC-06：对象方法（interpolate 等按要素属性取值）表达不了
          // heatmap-density 域 —— 契约外表达显式 evidence，不静默丢弃。
          recordSymbolLawEvidence("unmapped-paint-key", {
            key: "color",
            native: "heatmap-color",
            reason: "heatmap color must be a constant hex (density-ramp domain), got a StyleMethod object",
          }, layer.id);
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
      } else if (layerType === "fill-extrusion") {
        // ADR-0095: First-class 3D extrusion paint compiling
        if (layer.paint.color !== undefined)
          maplibreLayer.paint["fill-extrusion-color"] = compileStyleMethod(layer.paint.color);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["fill-extrusion-opacity"] = compileStyleMethod(layer.paint.opacity);
        if ((layer.paint as any).height !== undefined)
          maplibreLayer.paint["fill-extrusion-height"] = compileStyleMethod((layer.paint as any).height);
        if ((layer.paint as any).base !== undefined)
          maplibreLayer.paint["fill-extrusion-base"] = compileStyleMethod((layer.paint as any).base);

        // MapLibre native paint property passthrough (expressions / direct keys)
        for (const rawKey of [
          "fill-extrusion-color",
          "fill-extrusion-height",
          "fill-extrusion-base",
          "fill-extrusion-opacity",
        ] as const) {
          const rawValue = (layer.paint as Record<string, unknown>)[rawKey];
          if (rawValue !== undefined && maplibreLayer.paint[rawKey] === undefined) {
            maplibreLayer.paint[rawKey] = rawValue as unknown as StyleMethod;
          }
        }
      }
    }

    // ── AC-06 P1：符号律兜底 ────────────────────────────────────────────
    // spec 未显式给值的符号维度（radius/width/opacity）按 f(zoom, featureCount)
    // 出厂默认表兜底；要素数未知（url/dataPath 源）时回落出厂锚点 —— 与
    // live 路径 renderer.addThematicLayer 同一符号律，双路径不再漂移。
    const lawFilled: string[] = [];
    if (layerType === "circle") {
      if (maplibreLayer.paint["circle-radius"] === undefined) {
        maplibreLayer.paint["circle-radius"] = circleRadiusExpression({ featureCount });
        lawFilled.push("radius");
      }
      if (maplibreLayer.paint["circle-opacity"] === undefined) {
        maplibreLayer.paint["circle-opacity"] = opacityForCount({ featureCount });
        lawFilled.push("opacity");
      }
    } else if (layerType === "line") {
      if (maplibreLayer.paint["line-width"] === undefined) {
        maplibreLayer.paint["line-width"] = lineWidthExpression({ featureCount });
        lawFilled.push("width");
      }
    } else if (layerType === "fill") {
      if (maplibreLayer.paint["fill-opacity"] === undefined) {
        maplibreLayer.paint["fill-opacity"] = opacityForCount({ featureCount });
        lawFilled.push("opacity");
      }
    } else if (layerType === "heatmap") {
      if (maplibreLayer.paint["heatmap-radius"] === undefined) {
        const explicitRadius = (layer.paint as any)?.radius;
        maplibreLayer.paint["heatmap-radius"] = heatmapRadiusExpression({
          featureCount,
          baseRadiusPx: typeof explicitRadius === "number" ? explicitRadius : undefined,
        });
        lawFilled.push("heatmap-radius");
      }
    }
    if (lawFilled.length > 0) {
      recordSymbolLawEvidence("law-applied", { keys: lawFilled, featureCount }, layer.id);
    }

    // ── V4 point_cluster：活动簇三子层（MapLibre 官方聚簇范式）────────
    // base circle 层即「未聚类点」（filter 排除簇）；簇圆按 point_count
    // 分级半径/色深；计数标注复用 style 级 glyphs。
    // AC-06 P2：密度自动聚合 —— 显式 cluster 配置优先；无显式配置时由
    // 预扫描裁决（presentation.mode === cluster）注入空配置对象。
    const clusterCfg =
      (layer as any).cluster ?? ((layer as any).style?.cluster) ?? ((layer.paint as any)?.cluster)
      ?? (presentation?.mode === "cluster" ? {} : undefined);
    let labelLayerCountDelta = 0;
    if (clusterCfg && layerType === "circle") {
      maplibreLayer.filter = ["!", ["has", "point_count"]];
      // clusterCfg.radius 语义由 MapLibre cluster 源配置承载（这里不直接消费）。
      const clusterLayer: any = {
        id: `${layer.id}__clusters`,
        type: "circle",
        source: layer.source,
        // visibility 与主层联动（隐藏主层不得残留簇圆/计数）
        ...(layer.layout?.visibility ? { layout: { visibility: layer.layout.visibility } } : {}),
        filter: ["has", "point_count"],
        paint: {
          "circle-color": [
            "step", ["get", "point_count"],
            "#9ecae1", 10, "#6baed6", 50, "#3182bd", 200, "#08519c",
          ],
          "circle-radius": [
            "step", ["get", "point_count"],
            14, 10, 20, 50, 26, 200, 34,
          ],
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.9,
        },
      };
      if (srcDef?.type === "vector") {
        clusterLayer["source-layer"] = (layer as any).sourceLayer ?? "data";
      }
      compiledLayers.push(clusterLayer);

      const countLayer: any = {
        id: `${layer.id}__cluster-count`,
        type: "symbol",
        source: layer.source,
        ...(layer.layout?.visibility ? { layout: { visibility: layer.layout.visibility } } : {}),
        filter: ["has", "point_count"],
        layout: {
          "text-field": ["get", "point_count_abbreviated"],
          "text-size": 12,
          "text-allow-overlap": false,
        },
        paint: {
          "text-color": "#ffffff",
          "text-halo-color": "rgba(0,0,0,0.35)",
          "text-halo-width": 0.8,
        },
      };
      if (srcDef?.type === "vector") {
        countLayer["source-layer"] = (layer as any).sourceLayer ?? "data";
      }
      compiledLayers.push(countLayer);
      labelLayerCountDelta = 1;
      // 簇色板像素色（hex）进 legend 摘要（计数分级），有组件在场时由
      // 组件渲染；无组件时导出 HUD 图例兜底同链。
      const clusterLegend = {
        layerId: layer.id,
        title: (layer as any).label?.field ?? undefined,
        entries: [
          { color: "#9ecae1", label: "1–9" },
          { color: "#6baed6", label: "10–49" },
          { color: "#3182bd", label: "50–199" },
          { color: "#08519c", label: "200+" },
        ],
      };
      legends.push(clusterLegend as any);
    }

    compiledLayers.push(maplibreLayer);

    const legend = extractLegendForLayer(layer);
    if (legend) {
      legends.push(legend);
    }

    const labelSpec = layer.label || (layer.layout?.labelField ? { field: layer.layout.labelField } : undefined);
    // AC-06：无数据面层型（background/hillshade）不挂 label 子层。
    if (labelSpec && labelSpec.field && layer.type !== "background" && layer.type !== "hillshade") {
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
    if (labelLayerCountDelta > 0) {
      labelLayerCount += labelLayerCountDelta;
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
