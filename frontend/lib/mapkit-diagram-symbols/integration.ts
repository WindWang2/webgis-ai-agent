/**
 * integration.ts —— MapSpec 层 → live 图符 custom layer 的唯一接线面。
 *
 * 职责（runtime.ts 只做最小挂钩，逻辑全在本模块，保证可测）：
 * 1. 色板：镜像后端 palettes.py Qualitative 库存（Set1/Set2/Dark2/Pastel1，
 *    hex 逐位一致 —— 色值唯一权威原则，前端不自造色板）；
 * 2. 实例构建：资格抽取（omit 缺失会计；负值/非有限由 oracle typed 拒绝，
 *    与后端 SVG twin 同为整层披露语义）→ oracle 全链 → 布局 → 实例；
 * 3. attach/detach：custom layer id = `${layerId}-diagram-symbols`，
 *    rememberRuntimeRemount 注册闭包 —— basemap setStyle 重载后由
 *    remountRuntimeLayers 重放（scene3d/custom-layer 同款存活问题）；
 * 4. 诚实披露：源要素不可得（ref 占位/空 FC）→ 结构化 disclosure 返回值，
 *    绝不静默不画。
 */
import type { Map as MaplibreMap } from "maplibre-gl";
import {
  buildGlyphGeometry,
  computeGlyphScale,
  glyphBoundRadius,
  normalizeSizePolicy,
  type DiagramGlyph,
  type GlyphValue,
  type SizePolicyInput,
} from "./oracle";
import {
  solveDiagramSymbolLayout,
  type LayoutGlyph,
  type LayoutEvidence,
} from "./layout";
import { createDiagramSymbolCustomLayer, type DiagramGlyphInstance } from "./layer";
import {
  rememberRuntimeRemount,
  unregisterRuntimeLayer,
} from "@/lib/map-kit/runtime-layer-registry";

/** 后端 app/lib/cartography/palettes.py Qualitative 库存的逐位镜像。 */
export const QUALITATIVE_PALETTES: Record<string, string[]> = {
  Set1: ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
         "#ff7f00", "#ffff33", "#a65628", "#f781bf", "#999999"],
  Set2: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3",
         "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
  Dark2: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a",
          "#66a61e", "#e6ab02", "#a6761d", "#666666"],
  Pastel1: ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6",
            "#ffffcc", "#e5d8bd", "#fddaec", "#f2f2f2"],
};

export const DIAGRAM_SYMBOL_DEFAULT_PALETTE = "Set1";

export function resolvePaletteColors(paletteId: string | null | undefined): string[] {
  const key = typeof paletteId === "string" && QUALITATIVE_PALETTES[paletteId]
    ? paletteId : DIAGRAM_SYMBOL_DEFAULT_PALETTE;
  return QUALITATIVE_PALETTES[key];
}

export interface DiagramLayerSpecLike {
  id: string;
  source: string;
  diagram_symbol?: Record<string, unknown> | null;
}

export interface DiagramSourceDataLike {
  features?: unknown;
}

export interface QualifiedValues {
  values: GlyphValue[][];
  glyph_feature_ids: string[];
}

/**
 * 资格抽取（omit 路径；与 qualification.py 同语义：缺失键/null → omit +
 * 会计，字段名/数值性由 oracle 在几何构建时 typed 拒绝 —— 整层披露）。
 * 要素 id：GeoJSON `id` 成员 → properties[idField] 回退 → 位置序号
 * （与后端 id_field 缺省 "id" 对齐；无 id 的数据集在 apply 期已被后端
 * MISSING_FEATURE_ID 拒绝，live 侧位置回退只为容错重绘）。
 */
export function qualifyFeatureValues(
  features: Array<Record<string, unknown>>,
  categories: string[],
  fields: string[],
  idField = "id",
): { values: GlyphValue[][]; glyph_feature_ids: string[]; missing_by_category: Record<string, number> } {
  const values: GlyphValue[][] = [];
  const ids: string[] = [];
  const missing: Record<string, number> = {};
  for (const cat of categories) missing[cat] = 0;
  features.forEach((feature, idx) => {
    const props = (feature.properties ?? feature) as Record<string, unknown>;
    let fid = (feature as { id?: unknown }).id;
    if (fid === null || fid === undefined) fid = props[idField];
    const row: GlyphValue[] = [];
    categories.forEach((cat, i) => {
      const raw = props[fields[i]];
      if (raw === null || raw === undefined) {
        missing[cat] = (missing[cat] ?? 0) + 1;
        return;
      }
      if (typeof raw !== "number" || !Number.isFinite(raw)) {
        // review A#7：与 Python qualification 同语义 —— 不做静默转型。
        throw new Error(`FIELD_NOT_NUMERIC: category=${cat} got ${typeof raw}`);
      }
      row.push([cat, raw] as GlyphValue);
    });
    if (row.length > 0) {
      values.push(row);
      ids.push(String(fid ?? `pos:${idx}`));
    }
  });
  return { values, glyph_feature_ids: ids, missing_by_category: missing };
}

/** oracle 全链 → 图符实例（+ 布局证据）；分离导出便于测试。 */
export function buildGlyphInstancesForLayer(
  block: Record<string, unknown>,
  features: Array<Record<string, unknown>>,
): {
  instances: DiagramGlyphInstance[];
  evidence: LayoutEvidence;
  digest: string;
} {
  const kind = String(block.kind ?? "");
  const categories = Array.isArray(block.categories) ? block.categories.map(String) : [];
  const fields = Array.isArray(block.fields) ? block.fields.map(String) : [];
  const sizePolicy = normalizeSizePolicy(
    kind, (block.size_policy ?? null) as SizePolicyInput | null);
  const qualified = qualifyFeatureValues(
    features, categories, fields,
    typeof block.id_field === "string" ? block.id_field : "id");
  const scale = computeGlyphScale(kind, qualified.values, sizePolicy);
  const geometryById = new Map<string, DiagramGlyph>();
  const radii: number[] = [];
  qualified.values.forEach((row, i) => {
    const geo = buildGlyphGeometry(kind, row, scale, sizePolicy,
      { layout: typeof block.layout === "string" ? block.layout : undefined });
    geometryById.set(qualified.glyph_feature_ids[i], geo);
    radii.push(glyphBoundRadius(geo));
  });
  const featuresById = new Map<string, Record<string, unknown>>();
  features.forEach((feature, idx) => {
    const props = (feature.properties ?? feature) as Record<string, unknown>;
    let fid = (feature as { id?: unknown }).id;
    if (fid === null || fid === undefined) fid = props[typeof block.id_field === "string" ? block.id_field : "id"];
    featuresById.set(String(fid ?? `pos:${idx}`), feature);
  });
  const orderedFeatures = qualified.glyph_feature_ids
    .map((gid) => featuresById.get(gid))
    .filter((f): f is Record<string, unknown> => f !== undefined);
  const plan = solveDiagramSymbolLayout(orderedFeatures, qualified, radii, {});
  const palette = resolvePaletteColors(
    typeof block.palette_id === "string" ? block.palette_id : null);
  const instances: DiagramGlyphInstance[] = plan.glyphs.map((g: LayoutGlyph) => ({
    position: g.position,
    geometry: geometryById.get(g.feature_id) as DiagramGlyph,
    palette,
    anchor: g.anchor,
    leaderLine: g.leader_line === true,
    displacementPx: typeof g.displacement_px === "number" ? g.displacement_px : 0,
    radiusPx: g.radius_px,
    minZoom: typeof g.min_zoom === "number" ? g.min_zoom : undefined,
  }));
  return { instances, evidence: plan.evidence, digest: plan.digest };
}

const attachedByLayerId = new Map<string, { layerId: string }>();

/** 挂图符 custom layer（含 registry 重放闭包）；重复挂载先卸载旧的。 */
export function attachDiagramSymbolLayer(
  map: MaplibreMap,
  layer: DiagramLayerSpecLike,
  sourceData: DiagramSourceDataLike | null,
): { ok: true; count: number; evidence: LayoutEvidence }
  | { ok: false; code: string; message: string } {
  const block = layer.diagram_symbol;
  if (!block || typeof block !== "object") {
    return { ok: false, code: "DIAGRAM_NO_BLOCK", message: "layer has no diagram_symbol block" };
  }
  const features = sourceData?.features;
  if (!Array.isArray(features) || features.length === 0) {
    return {
      ok: false, code: "DIAGRAM_SOURCE_FEATURES_UNAVAILABLE",
      message: "source features unavailable (ref placeholder or empty) — glyph layer not drawn",
    };
  }
  detachDiagramSymbolLayer(map, layer.id);
  const built = buildGlyphInstancesForLayer(block, features as Array<Record<string, unknown>>);
  const glyphLayerId = `${layer.id}-diagram-symbols`;
  const glyphLayer = createDiagramSymbolCustomLayer({ id: glyphLayerId, glyphs: built.instances });
  if (map.getLayer(glyphLayerId)) {
    try { map.removeLayer(glyphLayerId); } catch { /* already gone */ }
  }
  map.addLayer(glyphLayer as never);
  attachedByLayerId.set(layer.id, { layerId: glyphLayerId });
  // basemap setStyle 重载后重放（闭包兜底路径 —— custom 系图层同款存活问题）
  rememberRuntimeRemount(glyphLayerId, (remountMap: MaplibreMap) => {
    if (!remountMap.getLayer(glyphLayerId)) {
      remountMap.addLayer(glyphLayer as never);
    }
  });
  return { ok: true, count: built.instances.length, evidence: built.evidence };
}

/** 卸图层专属图符层（主层删除/换 spec 时调用）。 */
export function detachDiagramSymbolLayer(map: MaplibreMap, layerId: string): void {
  const entry = attachedByLayerId.get(layerId);
  const glyphLayerId = `${layerId}-diagram-symbols`;
  if (map.getLayer(glyphLayerId)) {
    try { map.removeLayer(glyphLayerId); } catch { /* already gone */ }
  }
  if (entry) {
    unregisterRuntimeLayer(glyphLayerId);
    attachedByLayerId.delete(layerId);
  }
}
