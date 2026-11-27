/**
 * index.ts —— 地理锚定统计图符（feature-bound diagram symbols）公共 API。
 *
 * 三层结构：
 * - oracle.ts：Python geometry/contracts 的 TS 位级孪生（唯一几何权威）；
 * - layout.ts：Python layout 的 TS 孪生（锚定 + 确定性避让 + 全量会计）；
 * - layer.ts：MapLibre GL custom layer（消费 oracle/layout 输出绘制）；
 * - vertices.ts：几何 → GL 三角形顶点纯函数。
 *
 * mapspec-runtime 接线约定：图层 id 用 ``LAYER_ID_SUFFIX`` 拼接（如
 * ``${layerPlanId}-diagram-symbols``），实例数据 =
 * buildGlyphGeometry → solveDiagramSymbolLayout → createDiagramSymbolCustomLayer。
 */

export {
  // 常量
  GEOMETRY_DECIMALS,
  DIAGRAM_SYMBOL_KINDS,
  SERIES_BAR_LAYOUTS,
  MISSING_POLICIES,
  MAX_CATEGORIES,
  MIN_CATEGORIES,
  MIN_COMPOSITION_CATEGORIES,
  EXTREME_VALUE_RANGE_RATIO,
  EXTREME_RADIUS_RATIO,
  MAX_GLYPH_RADIUS_PX,
  MAX_GLYPH_EXTENT_PX,
  quantize3,
  pythonRound,
  // typed 错误
  DiagramSymbolError,
  // 合同（contracts.py 孪生）
  normalizeSizePolicy,
  computeGlyphScale,
  rowNumbers,
  // 几何（geometry.py 孪生）
  buildGlyphGeometry,
  glyphBoundRadius,
  geometryDigest,
  glyphToCanonical,
  // 类型
  type DiagramSymbolKind,
  type SeriesBarLayout,
  type GlyphValue,
  type ScaleCell,
  type ValueRow,
  type SizePolicyInput,
  type NormalizedSizePolicy,
  type GlyphScale,
  type PieSlice,
  type CompositionGlyph,
  type BarSegment,
  type SeriesBarsGlyph,
  type SegmentedSegment,
  type SegmentedGlyph,
  type DiagramGlyph,
  type DiagramErrorCode,
  type DiagramErrorOptions,
  type BuildGlyphOptions,
} from "./oracle";

export {
  // 常量
  MAX_LAYOUT_FEATURES,
  DEFAULT_MAX_GLYPHS,
  DEFAULT_COLLISION_PADDING_PX,
  DEFAULT_LEADER_THRESHOLD_PX,
  MAX_DISPLACEMENT_STEPS,
  NUDGE_DIRECTIONS,
  // Web Mercator
  lnglatToWorld,
  worldToLnglat,
  worldPxScale,
  // 锚定
  computeFeatureAnchor,
  normalizeLayoutPolicy,
  glyphMinZoom,
  // 布局求解
  solveDiagramSymbolLayout,
  layoutDigest,
  // 类型
  type AnchorGeometry,
  type DiagramFeatureInput,
  type AnchorResult,
  type LayoutPolicyInput,
  type NormalizedLayoutPolicy,
  type QualifiedInput,
  type LayoutGlyph,
  type BlockedDetail,
  type LayoutEvidence,
  type DiagramSymbolLayoutPlan,
} from "./layout";

export {
  parseHexColor,
  glyphAngleUnitVector,
  arcSegmentCount,
  pieSliceFan,
  donutRingStrip,
  barQuad,
  triangulateGlyph,
  type RgbaColor,
  type TriangulatedGlyph,
} from "./vertices";

export {
  createDiagramSymbolCustomLayer,
  DIAGRAM_SYMBOL_LAYER_DEFAULT_ID,
  type DiagramGlyphInstance,
  type DiagramSymbolLayerOptions,
  type DiagramSymbolCustomLayer,
} from "./layer";

/** mapspec-runtime 图层 id 拼接后缀。 */
export const LAYER_ID_SUFFIX = "-diagram-symbols";
