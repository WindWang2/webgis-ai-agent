import {
  MapSpec,
  MapSpecLayer,
  MapSpecSource,
  MapSpecCompileResult,
  CompileReport,
  CompileError,
  LegendDef,
  LegendItem,
  StyleMethod,
  SpatialMetaProfile,
} from "./types";
import type {
  ConstantStyleMethod,
  FieldStyleMethod,
  InterpolateStyleMethod,
  MatchStyleMethod,
  StepStyleMethod,
} from "./types.generated";

/** StyleMethod 的对象变体（排除标量别名的窄化形 —— 类型守卫谓词）。 */
type SemanticStyleMethod =
  | ConstantStyleMethod
  | InterpolateStyleMethod
  | StepStyleMethod
  | MatchStyleMethod
  | FieldStyleMethod;
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
} from "../map-kit/symbol-law";
// C11：Renderer ABI 类型边界 + 可见性/版本协商 + bivariate 校验。
// 相对导入（jiti/headless CLI 图不含 vite 别名解析 —— `@/` 在
// compile_via_cli 生产路径直接 Cannot find module，2026-09-29 实证）。
import {
  asExpression,
  asLayerSpecification,
  asPaintValue,
  asStyleSpecification,
  asCompiledStyleView,
  type CompiledLayer,
  type MapLibrePaint,
  type MapLibrePaintValue,
  type SourceSpecification,
} from "../carto-ir/style-abi";
import { resolveLayerVisibility } from "../carto-ir/visibility";
import { checkMapSpecVersion } from "../carto-ir/version";
import { checkBivariateLayer } from "../carto-ir/bivariate";

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

export function isStyleMethodObject(val: unknown): val is SemanticStyleMethod {
  return (
    val !== null &&
    typeof val === "object" &&
    "method" in val &&
    typeof (val as { method: unknown }).method === "string"
  );
}

/**
 * StyleMethod（语义方言）→ MapLibre 表达式/标量（Renderer ABI 值域）。
 * C11：返回类型锚到 MapLibrePaintValue（ExpressionSpecification |
 * string | number | boolean | null）—— 此前 `any` 让 paint 键改名只在
 * 运行时黑图暴露。构造的表达式数组经 asExpression 单一边界收口
 * （TS 无法从字面量数组推断判别联合）。
 */
export function compileStyleMethod(method: StyleMethod): MapLibrePaintValue;
export function compileStyleMethod(method: StyleMethod | undefined): MapLibrePaintValue | undefined;
export function compileStyleMethod(
  method: StyleMethod | undefined,
): MapLibrePaintValue | undefined {
  if (method === undefined) return undefined;
  if (!isStyleMethodObject(method)) {
    return method;
  }

  const m = method;
  switch (m.method) {
    case "constant":
      return m.value;

    case "field":
      return asExpression(["get", m.field]);

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
        return (m as { default?: string | number }).default ?? 0;
      }
      const flattenedStops: unknown[] = [];
      for (const [stopVal, outputVal] of stops) {
        flattenedStops.push(stopVal, outputVal);
      }
      return asExpression(["interpolate", interpolationExpr, fieldExpr, ...flattenedStops]);
    }

    case "step": {
      const fieldExpr = asExpression(["to-number", ["get", m.field]]);
      const stops = m.stops;
      if (!stops || stops.length === 0) {
        return m.default ?? 0;
      }
      const initialValue = m.default !== undefined ? m.default : stops[0][1];
      const flattenedStops: unknown[] = [];
      const startIndex = m.default !== undefined ? 0 : 1;
      for (let i = startIndex; i < stops.length; i++) {
        flattenedStops.push(stops[i][0], stops[i][1]);
      }
      return asExpression(["step", fieldExpr, initialValue, ...flattenedStops]);
    }

    case "match": {
      const fieldExpr = asExpression(["get", m.field]);
      const rawCases = m.cases;
      if (!Array.isArray(rawCases) || rawCases.length === 0) {
        return m.default ?? "";
      }
      const cases: unknown[] = [];
      for (const [caseVal, outputVal] of rawCases) {
        cases.push(caseVal, outputVal);
      }
      const defaultValue = m.default !== undefined ? m.default : cases[cases.length - 1] ?? "";
      return asExpression(["match", fieldExpr, ...cases, defaultValue]);
    }

    default:
      return asPaintValue((m as { value?: unknown }).value);
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
    if (!KNOWN_SOURCE_TYPES.has(source.type)) {
      errors.push({
        code: "UNKNOWN_SOURCE_TYPE",
        message: `Source "${sourceId}" has unknown type "${source.type}". Headless compilation refuses to emit a silent empty placeholder.`,
      });
      recordSymbolLawEvidence("unknown-source-type", { sourceType: source.type }, sourceId);
    }
  }

  // ADR-0199：scene.terrain 校验（与后端 coordinator.validate 同 fail-closed
  // 口径 —— 悬空源 / 非 raster-dem 源都是编译错误，绝不静默降级地形）。
  const sceneCfg = spec.scene;
  if (sceneCfg && typeof sceneCfg === "object" && sceneCfg.terrain) {
    const terrainSourceId = sceneCfg.terrain.source;
    const terrainSource =
      typeof terrainSourceId === "string"
        ? (spec.sources || {})[terrainSourceId]
        : undefined;
    if (!terrainSource) {
      errors.push({
        code: "SCENE_TERRAIN_SOURCE_REF",
        message: `scene.terrain references missing source "${String(terrainSourceId)}".`,
      });
    } else if (terrainSource.type !== "raster-dem") {
      errors.push({
        code: "SCENE_TERRAIN_SOURCE_TYPE",
        message: `scene.terrain source "${terrainSourceId}" is "${String(
          terrainSource.type
        )}", expected "raster-dem".`,
      });
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
        const m = styleMethod;

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
function boundsToImageCorners(
  bounds: [number, number, number, number],
): [[number, number], [number, number], [number, number], [number, number]] {
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
  const colorProp =
    layer.paint.color ?? (layer.paint as Record<string, unknown>)["fill-extrusion-color"];

  if (colorProp && isStyleMethodObject(colorProp)) {
    const m = colorProp;
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
  // C11：渲染 ABI 版本协商门 —— 词表外版本 fail-safe（与 live 路径同一
  // 免克隆判定 checkMapSpecVersion，S3 review P2-1/P2-2；产物为显式失败
  // 报告而非部分渲染）。
  const versionVerdict = checkMapSpecVersion(spec);
  if (!versionVerdict.ok) {
    return {
      style: asCompiledStyleView({ version: 8, sources: {}, layers: [] }),
      html: "",
      legend: [],
      report: {
        success: false,
        errors: [{
          code: `mapspec_${versionVerdict.reason}`,
          message: `spec version "${versionVerdict.version}" is not in the supported vocabulary; refusing to compile (fail-safe)`,
          field: "version",
        }],
        warnings: [],
        stats: { sourceCount: 0, layerCount: 0, compiledLayerCount: 0, labelLayerCount: 0 },
      },
    };
  }

  const { errors, warnings } = validateMapSpec(spec, profile);
  const success = errors.length === 0;

  // ── AC-06 P2：密度自适应表达切换（预扫描）───────────────────────────
  // 点层按 inlineData 要素数裁决 native/cluster/heatmap；cluster 自动落到
  // geojson 源配置（复用既有 __clusters 子层范式）；heatmap 改写编译层型。
  // 阈值以 VIEWPORT_RENDER_BUDGET=5000 / MVT 5000 为基准（symbol-law）。
  // review R2：共享源（多 layer 引用同一 geojson 源）不做自动切换 ——
  // 源级聚合会静默改写兄弟层的数据视图；仅单消费者源参与自适应。
  // review round-2：计数不分层型 —— 1 circle + 1 symbol/heatmap 共享同源时，
  // cluster:true 注入 geojson 源同样会改写兄弟层的数据视图。
  const layerCountPerSource = new Map<string, number>();
  for (const layer of spec.layers || []) {
    layerCountPerSource.set(layer.source, (layerCountPerSource.get(layer.source) ?? 0) + 1);
  }
  const autoClusterSourceIds = new Set<string>();
  const presentationByLayerId = new Map<string, DensityPresentation>();
  for (const layer of spec.layers || []) {
    if (layer.type !== "circle") continue;
    const srcDef = spec.sources?.[layer.source];
    if (srcDef?.type !== "geojson") continue;
    if ((layerCountPerSource.get(layer.source) ?? 0) !== 1) continue;
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
      // review R2：heatmap 改写会丢失分类色面 —— 带 legend_spec 的层显式
      // 披露图例分歧（图例描述分类、画布是密度热图）。
      recordSymbolLawEvidence("density-switch", {
        from: "native",
        to: "heatmap",
        featureCount: count,
        legend_divergence: !!layer.legend_spec,
      }, layer.id);
    } else {
      recordSymbolLawEvidence("presentation-decision", { mode: "native", featureCount: count }, layer.id);
    }
  }

  const sources: Record<string, SourceSpecification> = {};
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
      const dataRef: string = source.url || source.dataPath || "";
      sources[key] = {
        type: "geojson",
        data: dataRef,
        ...(source.cluster ? { cluster: true, clusterRadius: source.cluster.radius ?? 60, clusterMaxZoom: source.cluster.maxzoom ?? 14 } : {}),
      };
    } else if (source.type === "vector") {
      sources[key] = {
        type: "vector",
        tiles: source.tiles,
        minzoom: source.minzoom ?? 0,
        maxzoom: source.maxzoom ?? 14,
      };
    } else if (source.type === "raster-dem") {
      // AC-06 (ADR-0155)：hillshade 的数据面（raster-dem 源最小投影）。
      sources[key] = {
        type: "raster-dem",
        url: source.url,
        ...(source.tileSize !== undefined ? { tileSize: source.tileSize } : {}),
        ...(source.encoding !== undefined ? { encoding: source.encoding } : {}),
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

  const compiledLayers: CompiledLayer[] = [];
  const legends: LegendDef[] = [];
  let labelLayerCount = 0;
  // review P2-3：layout 透传存活的 text-field 也需要 style 级 glyphs。
  let hasPassthroughTextField = false;

  for (const layer of spec.layers || []) {
    const srcDef: MapSpecSource | undefined = spec.sources?.[layer.source];
    const features = srcDef?.type === "geojson" ? srcDef?.inlineData?.features : undefined;
    const featureCount: number | undefined = Array.isArray(features) ? features.length : undefined;
    // AC-06 P2：密度裁决为 heatmap 的 circle 层按 heatmap 编译（点数超出
    // 预算时逐点符号已无读性；spec 契约层型不变，只改写编译产物）。
    const presentation = presentationByLayerId.get(layer.id);
    const layerType =
      layer.type === "circle" && presentation?.mode === "heatmap" ? "heatmap" : layer.type;
    // C11 v1.5：显隐/zoom 单一裁决面（与 live 路径 addLayerSafe 同源）。
    // byte-parity：仅当 layout.visibility 显式存在或裁决为 "none"
    // （authored visible:false —— 此前无消费的契约漂移收口）才输出键；
    // 默认 "visible" 不落键（与既有编译产物逐字节一致）。
    const vis = resolveLayerVisibility(layer);
    if (vis.invalidLayoutVisibility !== undefined) {
      // S3 review P2-4b：词表外 layout.visibility 折算留痕（与 live 同证据）。
      recordSymbolLawEvidence("layout-visibility-invalid", {
        got: String(vis.invalidLayoutVisibility),
        resolved: vis.visibility,
      }, layer.id);
    }
    const maplibreLayer: CompiledLayer & { layout: MapLibrePaint } = {
      id: layer.id,
      type: layerType,
      // AC-06：background 层无数据面 —— 省略 source 键（MapLibre 契约）。
      ...(layer.type === "background" ? {} : { source: layer.source }),
      ...(layer.layout?.visibility || vis.visibility === "none"
        ? { layout: { visibility: vis.visibility } }
        : { layout: {} }),
      paint: {},
    };
    // Vector source layers require `source-layer` in MapLibre. MapSpecLayer
    // carries an optional `sourceLayer` passthrough; default to "data" (the
    // encoder's layer name in mvt.py).
    if (srcDef?.type === "vector") {
      maplibreLayer["source-layer"] = layer.sourceLayer ?? "data";
    }

    if (vis.gate.minzoom !== undefined) maplibreLayer.minzoom = vis.gate.minzoom;
    if (vis.gate.maxzoom !== undefined) maplibreLayer.maxzoom = vis.gate.maxzoom;

    // ADR-0199：3D 场景下 symbol 层默认面向视口（icon-pitch-alignment:
    // "viewport"）—— 透视地形上贴地符号会被压扁不可辨；spec 显式声明的
    // icon-pitch-alignment / icon-rotation-alignment 永不覆盖。
    const sceneMode = spec.scene?.mode;
    if (sceneMode === "3d" && layerType === "symbol") {
      const explicitLayout = layer.layout ?? {};
      if (explicitLayout["icon-pitch-alignment"] === undefined) {
        maplibreLayer.layout["icon-pitch-alignment"] = "viewport";
      }
      if (explicitLayout["icon-rotation-alignment"] === undefined) {
        maplibreLayer.layout["icon-rotation-alignment"] = "viewport";
      }
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
              ? (compileStyleMethod(rawValue) ?? null)
              : asPaintValue(rawValue);
          }
        }
        // ADR-0199：symbol 布局面显式声明透传（icon-*/text-*/symbol-* 布局键
        // 此前被 headless 编译静默丢弃 —— 与 paint 透传同款缺口收口）。
        // review P2-3：白名单前缀（任意键直传会把非 MapLibre 键塞进 style，
        // addLayer 校验失败 → 整层静默不渲染）+ StyleMethod 规范化（与 paint
        // 同口径）+ text-field 存活时补 glyphs（text 渲染的 style 级前置）。
        for (const [rawKey, rawValue] of Object.entries((layer.layout ?? {}) as Record<string, unknown>)) {
          if (rawKey === "visibility") continue;
          if (!(rawKey.startsWith("text-") || rawKey.startsWith("icon-") || rawKey.startsWith("symbol-"))) {
            recordSymbolLawEvidence("unmapped-paint-key", { key: rawKey, native: "(layout)", reason: "not a symbol-layer layout property" }, layer.id);
            continue;
          }
          if (rawValue !== undefined && maplibreLayer.layout[rawKey] === undefined) {
            maplibreLayer.layout[rawKey] = isStyleMethodObject(rawValue)
              ? (compileStyleMethod(rawValue) ?? null)
              : asPaintValue(rawValue);
            if (rawKey === "text-field") hasPassthroughTextField = true;
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
              ? (compileStyleMethod(rawValue) ?? null)
              : asPaintValue(rawValue);
          }
        }
      } else if (layerType === "heatmap") {
        // AC-06 review round-2：密度自适应改写（spec circle 层 → heatmap 编译
        // 产物）时，点符号语义键（radius/strokeColor/strokeWidth/blur）不再
        // 静默复用/丢弃 —— 显式 unmapped-key evidence 后忽略。radius 的 px
        // 语义是点径而非热力核半径（复用会把视觉半径放大若干倍），热力半径
        // 改由符号律锚点兜底；真 heatmap spec 层的 radius 映射不变。
        const rewrittenFromCircle = layer.type === "circle";
        if (rewrittenFromCircle) {
          const circleSemantics: Array<[string, string, string]> = [
            ["radius", "heatmap-radius", "point radius px is not a heatmap kernel radius"],
            ["strokeColor", "circle-stroke-color", "heatmap has no stroke face"],
            ["strokeWidth", "circle-stroke-width", "heatmap has no stroke face"],
            ["blur", "circle-blur", "heatmap density ramp has no blur face"],
          ];
          for (const [key, native, reason] of circleSemantics) {
            if ((layer.paint as Record<string, unknown>)[key] !== undefined) {
              recordSymbolLawEvidence("unmapped-paint-key", { key, native, reason }, layer.id);
            }
          }
        }
        if (!rewrittenFromCircle && layer.paint.radius !== undefined)
          maplibreLayer.paint["heatmap-radius"] = compileStyleMethod(layer.paint.radius);
        if (layer.paint.opacity !== undefined)
          maplibreLayer.paint["heatmap-opacity"] = compileStyleMethod(layer.paint.opacity);
        // heatmap-color 的输入必须是 heatmap-density，MapSpec 的高级 paint 方法
        // （interpolate 等按要素属性取值）表达不了。约定 paint.color 传常量热色
        // （raw hex 字符串），编译器生成 transparent→hot 的密度 ramp；缺省不动，
        // 保持 MapLibre 默认 ramp —— 测试需求不改生产默认值。
        const rawColor = layer.paint.color;
        // AC-06：常量 hex 的两种合法形态 —— 裸字符串与 constant 方法对象。
        const hotColor =
          typeof rawColor === "string"
            ? rawColor
            : isStyleMethodObject(rawColor) &&
                rawColor.method === "constant" &&
                typeof rawColor.value === "string"
              ? rawColor.value
              : undefined;
        if (hotColor !== undefined) {
          // 0.1 的密度阈值：让低密度区域也映到热色（孤立点场景可判定），
          // 0 处保持全透明。
          maplibreLayer.paint["heatmap-color"] = asExpression([
            "interpolate",
            ["linear"],
            ["heatmap-density"],
            0,
            "rgba(0,0,0,0)",
            0.1,
            hotColor,
            1,
            hotColor,
          ]);
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
            maplibreLayer.paint[rawKey] = isStyleMethodObject(rawValue)
              ? (compileStyleMethod(rawValue) ?? null)
              : asPaintValue(rawValue);
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
        const paintRecord = layer.paint as Record<string, unknown>;
        if (paintRecord.height !== undefined)
          maplibreLayer.paint["fill-extrusion-height"] = compileStyleMethod(paintRecord.height as StyleMethod);
        if (paintRecord.base !== undefined)
          maplibreLayer.paint["fill-extrusion-base"] = compileStyleMethod(paintRecord.base as StyleMethod);

        // MapLibre native paint property passthrough (expressions / direct keys)
        for (const rawKey of [
          "fill-extrusion-color",
          "fill-extrusion-height",
          "fill-extrusion-base",
          "fill-extrusion-opacity",
        ] as const) {
          const rawValue = (layer.paint as Record<string, unknown>)[rawKey];
          if (rawValue !== undefined && maplibreLayer.paint[rawKey] === undefined) {
            // C11：语义 StyleMethod dict 经编译降级（与 live paint-bridge
            // 同语义 —— 双路径方言漂移收口）；裸值 typed 直通。
            maplibreLayer.paint[rawKey] = isStyleMethodObject(rawValue)
              ? (compileStyleMethod(rawValue) ?? null)
              : asPaintValue(rawValue);
          }
        }
      }
    }

    // ── C11 v1.5：bivariate 原生声明的渲染端校验与降级 ─────────────────
    // 声明 ↔ paint 漂移（match 缺失/矩阵不支持/字段契约不完整）→ 常量色
    // 兜底 + warning 披露，绝不白图（编译产物 = 降级语义，不静默）。
    const bivCheck = layer.bivariate ? checkBivariateLayer(layer) : null;
    if (bivCheck && bivCheck.status === "degraded") {
      warnings.push(`bivariate_degraded[${bivCheck.reason}]: ${bivCheck.detail}`);
      const fb = bivCheck.fallbackColor;
      if (fb) {
        if (layerType === "fill") maplibreLayer.paint["fill-color"] = fb;
        else if (layerType === "circle") maplibreLayer.paint["circle-color"] = fb;
        else if (layerType === "line") maplibreLayer.paint["line-color"] = fb;
        else if (layerType === "fill-extrusion") maplibreLayer.paint["fill-extrusion-color"] = fb;
      }
    }

    // ── AC-06 P1：符号律兜底 ────────────────────────────────────────────
    // spec 未显式给值的符号维度（radius/width/opacity）按 f(zoom, featureCount)
    // 出厂默认表兜底；要素数未知（url/dataPath 源）时回落出厂锚点 —— 与
    // live 路径 renderer.addThematicLayer 同一符号律，双路径不再漂移。
    const lawFilled: string[] = [];
    if (layerType === "circle") {
      if (maplibreLayer.paint["circle-radius"] === undefined) {
        maplibreLayer.paint["circle-radius"] = asPaintValue(circleRadiusExpression({ featureCount }));
        lawFilled.push("radius");
      }
      if (maplibreLayer.paint["circle-opacity"] === undefined) {
        maplibreLayer.paint["circle-opacity"] = opacityForCount({ featureCount });
        lawFilled.push("opacity");
      }
    } else if (layerType === "line") {
      if (maplibreLayer.paint["line-width"] === undefined) {
        maplibreLayer.paint["line-width"] = asPaintValue(lineWidthExpression({ featureCount }));
        lawFilled.push("width");
      }
    } else if (layerType === "fill") {
      if (maplibreLayer.paint["fill-opacity"] === undefined) {
        maplibreLayer.paint["fill-opacity"] = opacityForCount({ featureCount });
        lawFilled.push("opacity");
      }
    } else if (layerType === "heatmap") {
      if (maplibreLayer.paint["heatmap-radius"] === undefined) {
        const explicitRadius = (layer.paint as Record<string, unknown>)?.radius;
        maplibreLayer.paint["heatmap-radius"] = asPaintValue(heatmapRadiusExpression({
          featureCount,
          // 密度改写层（spec circle → heatmap）不消费 circle radius ——
          // 点径≠核半径（已发 unmapped-key evidence），用出厂锚点兜底。
          baseRadiusPx:
            layer.type !== "circle" && typeof explicitRadius === "number"
              ? explicitRadius
              : undefined,
        }));
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
      layer.cluster ?? ((layer.paint as Record<string, unknown> | undefined)?.cluster as
        | Record<string, unknown>
        | undefined)
      ?? (presentation?.mode === "cluster" ? {} : undefined);
    let labelLayerCountDelta = 0;
    if (clusterCfg && layerType === "circle") {
      maplibreLayer.filter = ["!", ["has", "point_count"]];
      // clusterCfg.radius 语义由 MapLibre cluster 源配置承载（这里不直接消费）。
      // C11 v1.5：visibility/zoom 门与主层同一裁决面（authored
      // visible:false 时子层同隐 —— 此前只认 layout.visibility）。
      // 仅在 none 时输出 layout 键（byte parity：默认可见与既有产物一致）。
      // Record<string, never> 替代 {} 字面量空对象类型（eslint
      // no-empty-object-type；语义不变：默认可见时不输出 layout 键）。
      const subVisLayout: { layout: { visibility: "none" } } | Record<string, never> =
        vis.visibility === "none" ? { layout: { visibility: "none" } } : {};
      const subGate: Pick<CompiledLayer, "minzoom" | "maxzoom"> = {
        ...(vis.gate.minzoom !== undefined ? { minzoom: vis.gate.minzoom } : {}),
        ...(vis.gate.maxzoom !== undefined ? { maxzoom: vis.gate.maxzoom } : {}),
      };
      const clusterLayer: CompiledLayer = {
        id: `${layer.id}__clusters`,
        type: "circle",
        source: layer.source,
        // visibility 与主层联动（隐藏主层不得残留簇圆/计数）
        ...subVisLayout,
        ...subGate,
        filter: ["has", "point_count"],
        paint: {
          "circle-color": asExpression([
            "step", ["get", "point_count"],
            "#9ecae1", 10, "#6baed6", 50, "#3182bd", 200, "#08519c",
          ]),
          "circle-radius": asExpression([
            "step", ["get", "point_count"],
            14, 10, 20, 50, 26, 200, 34,
          ]),
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.9,
        },
      };
      if (srcDef?.type === "vector") {
        clusterLayer["source-layer"] = layer.sourceLayer ?? "data";
      }
      compiledLayers.push(clusterLayer);

      const countLayer: CompiledLayer = {
        id: `${layer.id}__cluster-count`,
        type: "symbol",
        source: layer.source,
        ...subGate,
        filter: ["has", "point_count"],
        layout: {
          "text-field": asExpression(["get", "point_count_abbreviated"]),
          "text-size": 12,
          "text-allow-overlap": false,
          // P1（S3 review）：visibility 须在 layout 字面量内 —— 字面量后的
          // spread 会被整体覆盖（死代码），cluster-count 的子层同隐此前失效。
          ...(vis.visibility === "none" ? { visibility: "none" as const } : {}),
        },
        paint: {
          "text-color": "#ffffff",
          "text-halo-color": "rgba(0,0,0,0.35)",
          "text-halo-width": 0.8,
        },
      };
      if (srcDef?.type === "vector") {
        countLayer["source-layer"] = layer.sourceLayer ?? "data";
      }
      compiledLayers.push(countLayer);
      labelLayerCountDelta = 1;
      // 簇色板像素色（hex）进 legend 摘要（计数分级），有组件在场时由
      // 组件渲染；无组件时导出 HUD 图例兜底同链。
      const clusterLegend = {
        layerId: layer.id,
        title: layer.label?.field ?? undefined,
        entries: [
          { color: "#9ecae1", label: "1–9" },
          { color: "#6baed6", label: "10–49" },
          { color: "#3182bd", label: "50–199" },
          { color: "#08519c", label: "200+" },
        ],
      };
      // 既有形状漂移（entries vs LegendDef.items —— 簇计数图例从未进导出
      // 图例链；P3 记录，不改行为）。typed 收口见 PR 审查注记。
      legends.push(clusterLegend as unknown as LegendDef);
    }

    compiledLayers.push(maplibreLayer);

    const legend = extractLegendForLayer(layer);
    if (legend) {
      legends.push(legend);
    }

    const labelSpec = layer.label || (layer.layout?.labelField ? { field: layer.layout.labelField } : undefined);
    // AC-06：无数据面层型（background/hillshade）不挂 label 子层；ac-05：raster/heatmap 同样排除
    // （此前编译器生成、运行时排除，同 spec 屏幕与导出漂移）。
    if (
      labelSpec &&
      labelSpec.field &&
      layer.type !== "raster" &&
      layer.type !== "heatmap" &&
      layer.type !== "background" &&
      layer.type !== "hillshade"
    ) {
      labelLayerCount++;
      const labelLayer: CompiledLayer = {
        id: `${layer.id}-label`,
        type: "symbol",
        source: layer.source,
        // C11 v1.5：标注子层继承主层 zoom 门（显隐经 layout.visibility）。
        ...(vis.gate.minzoom !== undefined ? { minzoom: vis.gate.minzoom } : {}),
        ...(vis.gate.maxzoom !== undefined ? { maxzoom: vis.gate.maxzoom } : {}),
        layout: {
          "text-field": asExpression(["get", labelSpec.field]),
          "text-size": compileStyleMethod(labelSpec.size ?? layer.layout?.labelSize ?? 12),
          "text-allow-overlap": false,
          ...(vis.visibility === "none" ? { visibility: "none" as const } : {}),
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
        labelLayer["source-layer"] = layer.sourceLayer ?? "data";
      }

      compiledLayers.push(labelLayer);
    }
    if (labelLayerCountDelta > 0) {
      labelLayerCount += labelLayerCountDelta;
    }
  }

  const center = spec.view?.center ?? [0, 0];
  const zoom = spec.view?.zoom ?? 2;

  // C11：style 产物锚到官方 StyleSpecification（CompiledStyle 构建形 +
  // asStyleSpecification 单一收口）；图层经 asLayerSpecification 收口。
  const style = asStyleSpecification({
    version: 8,
    name: "MapSpec Compiled Style",
    center: center as [number, number],
    zoom,
    bearing: spec.view?.bearing ?? 0,
    pitch: spec.view?.pitch ?? 0,
    sources,
    layers: compiledLayers.map(asLayerSpecification),
  });
  if (labelLayerCount > 0 || hasPassthroughTextField) {
    // symbol 图层的 text-field 在 MapLibre 里要求 style 级 glyphs 模板，
    // 否则运行时报错（symbol-label 场景暴露的真实编译缺陷）。
    // #1007：URL 进配置（NEXT_PUBLIC_MAP_GLYPHS_URL），支持本地字形托管。
    (style as Record<string, unknown>).glyphs = MAP_GLYPHS_URL;
  }

  // ADR-0199：scene.terrain → MapLibre style terrain 投影（校验已在
  // validateMapSpec 完成 —— 这里只在源合法时投影；非法时 errors 非空、
  // success=false，绝不静默降级）。
  const sceneCfg = spec.scene;
  if (sceneCfg && typeof sceneCfg === "object" && sceneCfg.terrain) {
    const terrain = sceneCfg.terrain as { source?: unknown; exaggeration?: unknown };
    const srcId = typeof terrain.source === "string" ? terrain.source : "";
    const src: MapSpecSource | undefined = (spec.sources || {})[srcId];
    if (srcId && src && src.type === "raster-dem") {
      const exaggeration =
        typeof terrain.exaggeration === "number" && terrain.exaggeration > 0
          ? terrain.exaggeration
          : 1.0; // 默认诚实比例（不放大）
      (style as Record<string, unknown>).terrain = {
        source: srcId,
        exaggeration,
      };
    }
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
    style: asCompiledStyleView(style),
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
