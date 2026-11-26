/**
 * layout.ts —— Python ``app/lib/cartography/diagram_symbols/layout.py`` 的
 * TS 位级孪生（WP2 图符布局：锚定、确定性避让、密度预算、全量会计）。
 *
 * 锚定策略（可解释、可披露）：
 * - Point → 坐标原样（``point``）；
 * - Polygon/MultiPolygon → 代表点（``polygon_representative_point``）；
 *   TS 侧无 shapely/GEOS，采用确定性 scanline 算法：取所选多边形 bbox
 *   中线 y，对所有环做 even-odd 求交，取**最宽内部跨段**的中点（GEOS
 *   InteriorPointArea 同语义；多跨段并列时取最左 —— 与 GEOS 观测一致，
 *   见 layout.test.ts 的 U 形 case）。已知偏差（均为建模事实而非缺陷）：
 *   1) MultiPolygon 组件选择按最大 bbox 面积（GEOS 按最大跨段宽度）；
 *   2) 不做拓扑有效性校验（Python 走 shapely is_valid + explain_validity，
 *      非法几何在 Python 侧 blocked、TS 侧按坐标结构校验近似）；
 *   3) 零高度退化环（面积为 0）GEOS 仍返回内部点，TS 侧回落到首个外环
 *      顶点。parity 要求：POINT 锚点逐位相等；多边形锚点只要求
 *      "落在多边形内 + 确定性"（golden parity 用正方形 case 锁位级）。
 * - LineString/MultiLineString → 参数 0.5 归一化插值（``line_midpoint``；
 *   MultiLineString 按组件顺序累计长度走查，与 GEOS 观测行为一致）。
 *
 * 避让算法（确定性，无随机）：Web Mercator 参考缩放级别下按优先级
 * （Σ|value| 降序，平局 feature_id 字典序升序）贪心放置；冲突时沿 8 方位
 * 螺旋位移（步长 = (r+padding)/2，最多 12 步），位移超阈值标
 * ``leader_line``；仍冲突 → 隐藏并计入 ``omitted_collision``。
 *
 * digest = sha256(canonical_json(glyphs)) 前 32 hex（canonical.ts 保证与
 * Python canonical_json 逐位一致 —— fixture layout_determinism 锁全值）。
 */

import {
  canonicalJson,
  fsum,
  pyFloat,
  pyInt,
  sha256Hex,
  type CanonicalValue,
} from "./canonical";
import type { GlyphValue } from "./oracle";
import { DiagramSymbolError, pythonRound, quantize3 } from "./oracle";

// ── 常量（与 layout.py 逐字对齐）────────────────────────────────────

export const MAX_LAYOUT_FEATURES = 20_000;
export const DEFAULT_MAX_GLYPHS = 1_000;
/** 包络间最小净距（px @参考缩放）。 */
export const DEFAULT_COLLISION_PADDING_PX = 2.0;
/** 位移超过该值（px）→ 标记引线。 */
export const DEFAULT_LEADER_THRESHOLD_PX = 6.0;
/** 8 方位螺旋位移的步数上限。 */
export const MAX_DISPLACEMENT_STEPS = 12;

/** Web Mercator 常数（EPSG:3857 球面版，与 MapLibre 同口径）。 */
const WEB_MERCATOR_BASE_PX = 256.0;
const WEB_MERCATOR_MAX_LAT = 85.05112878;

const SQRT2_2 = Math.sqrt(2.0) / 2.0;
/** 8 方位单位向量（E→NE→N→NW→W→SW→S→SE；固定顺序 = 确定性）。 */
export const NUDGE_DIRECTIONS: readonly (readonly [number, number])[] = [
  [1.0, 0.0], [SQRT2_2, SQRT2_2], [0.0, 1.0], [-SQRT2_2, SQRT2_2],
  [-1.0, 0.0], [-SQRT2_2, -SQRT2_2], [0.0, -1.0], [SQRT2_2, -SQRT2_2],
];

// ── Web Mercator ─────────────────────────────────────────────────────

/** (lng,lat) → Web Mercator 归一化平面 (0..1)。纬度越界 typed 拒绝。 */
export function lnglatToWorld(lng: number, lat: number): [number, number] {
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
    throw new DiagramSymbolError(
      "INVALID_COORDINATES",
      `锚点坐标非有限：lng=${String(lng)} lat=${String(lat)}`,
      { correctionHint: "几何锚点必须为有限数值" },
    );
  }
  if (!(-180.0 <= lng && lng <= 180.0)) {
    throw new DiagramSymbolError("INVALID_COORDINATES", `经度越界 lng=${lng}`, {
      correctionHint: "WGS84 经度域 [-180, 180]",
    });
  }
  if (!(-WEB_MERCATOR_MAX_LAT <= lat && lat <= WEB_MERCATOR_MAX_LAT)) {
    throw new DiagramSymbolError("INVALID_COORDINATES", `纬度超出 Web Mercator 域 lat=${lat}`, {
      correctionHint: `Web Mercator 纬度域 ±${WEB_MERCATOR_MAX_LAT}`,
    });
  }
  const x = (lng + 180.0) / 360.0;
  const sinLat = Math.sin((lat * Math.PI) / 180.0);
  const y = 0.5 - Math.log((1.0 + sinLat) / (1.0 - sinLat)) / (4.0 * Math.PI);
  return [x, y];
}

/** 归一化平面 → (lng,lat)（位移回写用）。 */
export function worldToLnglat(x: number, y: number): [number, number] {
  const lng = x * 360.0 - 180.0;
  const n = Math.PI * (1.0 - 2.0 * y);
  const lat = (Math.atan(Math.sinh(n)) * 180.0) / Math.PI;
  return [lng, lat];
}

/** 该缩放级别下全世界对应的像素宽度。 */
export function worldPxScale(zoom: number): number {
  return WEB_MERCATOR_BASE_PX * Math.pow(2.0, zoom);
}

/** Python round(v, 9) 的精确孪生（见 oracle.pythonRound）。 */
function round9(v: number): number {
  return pythonRound(v, 9);
}

// ── 锚定 ─────────────────────────────────────────────────────────────

export interface AnchorGeometry {
  type?: unknown;
  coordinates?: unknown;
}

export interface DiagramFeatureInput {
  geometry?: AnchorGeometry | null;
  [key: string]: unknown;
}

export type AnchorResult =
  | { anchor: [number, number]; anchor_source: string }
  | { blocked_reason: string; detail?: string };

type Pt = readonly [number, number];

function isPoint2(v: unknown): v is [number, number] {
  return Array.isArray(v) && v.length >= 2 && typeof v[0] === "number" && typeof v[1] === "number";
}

function ringEdgesClosed(ring: unknown): Pt[] | null {
  if (!Array.isArray(ring) || ring.length === 0) return null;
  const pts: Pt[] = [];
  for (const p of ring) {
    if (!isPoint2(p)) return null;
    pts.push([p[0], p[1]]);
  }
  return pts;
}

function evenOddSpansAtY(pts: Pt[], y: number): number[] {
  // even-odd 求交：边 (y1>y) !== (y2>y) 计交点 x；排序后相邻配对即内部跨段。
  const xs: number[] = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % n];
    if (y1 === y2) continue;
    if (y1 > y !== y2 > y) {
      xs.push(x1 + ((y - y1) * (x2 - x1)) / (y2 - y1));
    }
  }
  xs.sort((a, b) => a - b);
  return xs;
}

/**
 * 单多边形代表点：外环 bbox 中线 y 为扫描线，所有环 even-odd 求交，
 * 取最宽跨段中点（跨段并列取最左，与 GEOS 观测一致）。
 * 失败（退化/无交）回落外环首顶点并返回 false 让调用方决定。
 */
function polygonRepresentativePoint(rings: unknown): { point: Pt; degenerate: boolean } | null {
  if (!Array.isArray(rings) || rings.length === 0) return null;
  const outer = ringEdgesClosed(rings[0]);
  if (outer === null || outer.length < 3) return null;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const [, y] of outer) {
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  const allRings: Pt[][] = [];
  for (const ring of rings) {
    const pts = ringEdgesClosed(ring);
    if (pts === null) return null;
    allRings.push(pts);
  }
  const scanYs = [0.5, 0.25, 0.75, 0.125, 0.375, 0.625, 0.875];
  for (const frac of scanYs) {
    const y = minY + (maxY - minY) * frac;
    const xs: number[] = [];
    for (const ring of allRings) xs.push(...evenOddSpansAtY(ring, y));
    if (xs.length < 2 || xs.length % 2 !== 0) continue;
    let bestLo = 0;
    let bestHi = 0;
    let bestWidth = -1;
    for (let i = 0; i + 1 < xs.length; i += 2) {
      const width = xs[i + 1] - xs[i];
      if (width > bestWidth) {
        bestWidth = width;
        bestLo = xs[i];
        bestHi = xs[i + 1];
      }
    }
    if (bestWidth > 0 || (bestLo === bestHi && xs.length >= 2)) {
      return { point: [(bestLo + bestHi) / 2, y], degenerate: false };
    }
  }
  // 零高度退化环：回落外环首顶点（确定性；GEOS 行为不同 —— 已知偏差）。
  return { point: [outer[0][0], outer[0][1]], degenerate: true };
}

/** MultiPolygon：选最大 bbox 面积的组件（GEOS 按最大跨段宽 —— 已知偏差）。 */
function multiPolygonRepresentativePoint(polygons: unknown):
  | { point: Pt; degenerate: boolean }
  | null {
  if (!Array.isArray(polygons) || polygons.length === 0) return null;
  let best: { point: Pt; degenerate: boolean } | null = null;
  let bestArea = -Infinity;
  for (const rings of polygons) {
    if (!Array.isArray(rings) || rings.length === 0) return null;
    const outer = ringEdgesClosed(rings[0]);
    if (outer === null || outer.length < 3) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const [x, y] of outer) {
      minX = Math.min(minX, x); minY = Math.min(minY, y);
      maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
    const area = (maxX - minX) * (maxY - minY);
    if (area > bestArea) {
      const rep = polygonRepresentativePoint(rings);
      if (rep === null) return null;
      bestArea = area;
      best = rep;
    }
  }
  return best;
}

/** 线 0.5 归一化插值：按顶点对在半程长度处插值（MultiLineString 累计走查）。 */
function lineMidpoint(coords: unknown): Pt | null {
  if (!Array.isArray(coords) || coords.length === 0) return null;
  // MultiLineString：组件数组；LineString：单组件（组件元素是 [x,y]）。
  const components: Pt[][] = [];
  if (isPoint2(coords[0])) {
    const pts = ringEdgesClosed(coords);
    if (pts === null) return null;
    components.push(pts);
  } else {
    for (const part of coords) {
      const pts = ringEdgesClosed(part);
      if (pts === null || pts.length === 0) return null;
      components.push(pts);
    }
  }
  const first = components[0][0];
  let total = 0.0;
  for (const pts of components) {
    for (let i = 0; i + 1 < pts.length; i++) {
      total += Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
    }
  }
  if (!(total > 0)) return [first[0], first[1]];
  let target = total / 2.0;
  for (const pts of components) {
    for (let i = 0; i + 1 < pts.length; i++) {
      const segLen = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
      if (target <= segLen) {
        const t = segLen === 0 ? 0 : target / segLen;
        return [pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1])];
      }
      target -= segLen;
    }
  }
  // 浮点余差：回落到最后一个顶点。
  const lastComp = components[components.length - 1];
  const last = lastComp[lastComp.length - 1];
  return [last[0], last[1]];
}

/**
 * 要素 → 锚点（typed：anchor/anchor_source 或 blocked_reason）。
 * 与 Python compute_feature_anchor 同构（多边形算法偏差见文件头注释）。
 */
export function computeFeatureAnchor(feature: DiagramFeatureInput): AnchorResult {
  const geom = feature.geometry;
  if (typeof geom !== "object" || geom === null || Array.isArray(geom) || geom.coordinates == null) {
    return { blocked_reason: "missing_geometry" };
  }
  const gtype = geom.type;
  const coords = geom.coordinates;
  if (gtype === "Point") {
    if (!isPoint2(coords)) {
      return { blocked_reason: "invalid_geometry", detail: "point coordinates malformed" };
    }
    return { anchor: [coords[0], coords[1]], anchor_source: "point" };
  }
  if (gtype === "Polygon" || gtype === "MultiPolygon") {
    const rep =
      gtype === "Polygon"
        ? polygonRepresentativePoint(coords)
        : multiPolygonRepresentativePoint(coords);
    if (rep === null) {
      return { blocked_reason: "invalid_geometry", detail: "polygon coordinates malformed" };
    }
    return {
      anchor: [rep.point[0], rep.point[1]],
      anchor_source: "polygon_representative_point",
    };
  }
  if (gtype === "LineString" || gtype === "MultiLineString") {
    const mid = lineMidpoint(coords);
    if (mid === null) {
      return { blocked_reason: "invalid_geometry", detail: "line coordinates malformed" };
    }
    return { anchor: [mid[0], mid[1]], anchor_source: "line_midpoint" };
  }
  return { blocked_reason: "unsupported_geometry_type", detail: String(gtype) };
}

// ── 布局求解 ─────────────────────────────────────────────────────────

export interface LayoutPolicyInput {
  max_glyphs?: number;
  hide_below_radius_px?: number;
  collision_padding_px?: number;
  leader_threshold_px?: number;
  min_zoom_floor?: number;
  displacement_enabled?: boolean;
  reference_zoom?: number;
}

export interface NormalizedLayoutPolicy {
  max_glyphs: number;
  hide_below_radius_px: number;
  collision_padding_px: number;
  leader_threshold_px: number;
  min_zoom_floor: number;
  displacement_enabled: boolean;
  reference_zoom: number;
}

export interface QualifiedInput {
  /** 每个要素一行、按类别序对齐的 (category, value) 对。 */
  values: readonly (readonly GlyphValue[])[];
  glyph_feature_ids: readonly string[];
}

export interface LayoutGlyph {
  feature_id: string;
  anchor: [number, number];
  anchor_source: string;
  position: [number, number];
  displacement_px: number;
  leader_line: boolean;
  radius_px: number;
  min_zoom: number;
  priority_rank: number;
}

export interface BlockedDetail {
  feature_id: string;
  blocked_reason: string;
  detail?: string;
}

export interface LayoutEvidence {
  candidates: number;
  placed: number;
  omitted_collision: number;
  omitted_budget: number;
  omitted_small: number;
  blocked: number;
  blocked_detail: BlockedDetail[];
  blocked_truncated: boolean;
  displaced: number;
  leader_lines: number;
  anchor_source_counts: Record<string, number>;
  features_total: number;
  policy: NormalizedLayoutPolicy;
}

export interface DiagramSymbolLayoutPlan {
  schema: "diagram_symbol_layout.v1";
  glyphs: LayoutGlyph[];
  evidence: LayoutEvidence;
  digest: string;
}

export function normalizeLayoutPolicy(
  policy?: LayoutPolicyInput | null,
): NormalizedLayoutPolicy {
  const p: LayoutPolicyInput = policy ?? {};
  const maxGlyphs = p.max_glyphs ?? DEFAULT_MAX_GLYPHS;
  if (typeof maxGlyphs !== "number" || !Number.isInteger(maxGlyphs) || maxGlyphs < 1) {
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `layout_policy.max_glyphs 必须是正整数，got ${String(maxGlyphs)}`,
      { correctionHint: `1 ≤ max_glyphs ≤ ${MAX_LAYOUT_FEATURES}` },
    );
  }
  if (maxGlyphs > MAX_LAYOUT_FEATURES) {
    throw new DiagramSymbolError(
      "BUDGET_EXCEEDED",
      `max_glyphs=${maxGlyphs} 超过硬帽 ${MAX_LAYOUT_FEATURES}（无界图符数量拒绝）`,
    );
  }
  const hideBelow = p.hide_below_radius_px ?? 0.0;
  if (typeof hideBelow !== "number" || !Number.isFinite(hideBelow) || hideBelow < 0) {
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `hide_below_radius_px 非法：${String(hideBelow)}`,
    );
  }
  const padding = p.collision_padding_px ?? DEFAULT_COLLISION_PADDING_PX;
  if (typeof padding !== "number" || !Number.isFinite(padding) || padding < 0) {
    throw new DiagramSymbolError("INVALID_SIZE_POLICY", `collision_padding_px 非法：${String(padding)}`);
  }
  const leaderThr = p.leader_threshold_px ?? DEFAULT_LEADER_THRESHOLD_PX;
  if (typeof leaderThr !== "number" || !Number.isFinite(leaderThr) || leaderThr < 0) {
    throw new DiagramSymbolError("INVALID_SIZE_POLICY", `leader_threshold_px 非法：${String(leaderThr)}`);
  }
  const minZoomFloor = p.min_zoom_floor ?? 0;
  if (
    typeof minZoomFloor !== "number" || !Number.isInteger(minZoomFloor) ||
    minZoomFloor < 0 || minZoomFloor > 22
  ) {
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `min_zoom_floor 非法：${String(minZoomFloor)}`,
      { correctionHint: "取 [0, 22] 整数" },
    );
  }
  const referenceZoom = p.reference_zoom ?? 12.0;
  if (typeof referenceZoom !== "number" || !Number.isFinite(referenceZoom)) {
    // 已知偏差（收紧）：Python float() 对 NaN 会静默产出 NaN scale；TS 侧 typed 拒绝。
    throw new DiagramSymbolError(
      "INVALID_SIZE_POLICY",
      `reference_zoom 非法：${String(referenceZoom)}`,
    );
  }
  return {
    max_glyphs: maxGlyphs,
    hide_below_radius_px: hideBelow,
    collision_padding_px: padding,
    leader_threshold_px: leaderThr,
    min_zoom_floor: minZoomFloor,
    displacement_enabled: Boolean(p.displacement_enabled ?? true),
    reference_zoom: referenceZoom,
  };
}

/** 小图符抬升 min_zoom：<4px → floor+2；<8px → floor+1；否则 floor（cap 22）。 */
export function glyphMinZoom(radiusPx: number, floor: number): number {
  if (radiusPx < 4.0) return Math.min(floor + 2, 22);
  if (radiusPx < 8.0) return Math.min(floor + 1, 22);
  return floor;
}

function glyphsToCanonical(glyphs: readonly LayoutGlyph[]): CanonicalValue {
  return glyphs.map((g): CanonicalValue => ({
    feature_id: g.feature_id,
    anchor: [pyFloat(g.anchor[0]), pyFloat(g.anchor[1])],
    anchor_source: g.anchor_source,
    position: [pyFloat(g.position[0]), pyFloat(g.position[1])],
    displacement_px: pyFloat(g.displacement_px),
    leader_line: g.leader_line,
    radius_px: pyFloat(g.radius_px),
    min_zoom: pyInt(g.min_zoom),
    priority_rank: pyInt(g.priority_rank),
  }));
}

/** 与 Python _layout_digest 对齐：sha256(canonical_json(glyphs)) 前 32 hex。 */
export function layoutDigest(glyphs: readonly LayoutGlyph[]): string {
  return sha256Hex(canonicalJson(glyphsToCanonical(glyphs))).slice(0, 32);
}

/**
 * 确定性图符布局（schema ``diagram_symbol_layout.v1``）。
 * features / qualified.values / boundRadii 三者同序等长。
 */
export function solveDiagramSymbolLayout(
  features: readonly DiagramFeatureInput[],
  qualified: QualifiedInput,
  boundRadii: readonly number[],
  layoutPolicy?: LayoutPolicyInput | null,
): DiagramSymbolLayoutPlan {
  const policy = normalizeLayoutPolicy(layoutPolicy);
  const values = qualified.values;
  const ids = qualified.glyph_feature_ids;
  if (!(features.length === values.length && values.length === boundRadii.length)) {
    throw new DiagramSymbolError(
      "FIELD_NOT_FOUND",
      `features(${features.length})/values(${values.length})` +
        `/bound_radii(${boundRadii.length}) 数量不一致`,
      { correctionHint: "三者必须同序等长（qualification 输出直接喂入）" },
    );
  }
  if (features.length > MAX_LAYOUT_FEATURES) {
    throw new DiagramSymbolError(
      "BUDGET_EXCEEDED",
      `要素数 ${features.length} 超过硬帽 ${MAX_LAYOUT_FEATURES}`,
      { correctionHint: "先聚合/抽稀（有会计），不要无界渲染" },
    );
  }
  const idSet = new Set(ids);
  if (idSet.size !== ids.length) {
    throw new DiagramSymbolError(
      "DUPLICATE_CATEGORY",
      "glyph_feature_ids 存在重复（图符 identity 必须稳定唯一）",
      { correctionHint: "检查数据集 id 唯一性" },
    );
  }

  // 1) 锚定 + 阻塞收集
  interface Candidate {
    feature_id: string;
    wx: number;
    wy: number;
    anchor: [number, number];
    anchor_source: string;
    radius_px: number;
    importance: number;
  }
  const candidates: Candidate[] = [];
  const blocked: BlockedDetail[] = [];
  const anchorCounts: Record<string, number> = {};
  for (let idx = 0; idx < features.length; idx++) {
    const fid = String(ids[idx]);
    const anchorRes = computeFeatureAnchor(features[idx]);
    if ("blocked_reason" in anchorRes) {
      const entry: BlockedDetail = { feature_id: fid, blocked_reason: anchorRes.blocked_reason };
      if (anchorRes.detail !== undefined) entry.detail = anchorRes.detail;
      blocked.push(entry);
      continue;
    }
    const lng = anchorRes.anchor[0];
    const lat = anchorRes.anchor[1];
    let wx: number;
    let wy: number;
    try {
      [wx, wy] = lnglatToWorld(lng, lat);
    } catch (exc) {
      if (exc instanceof DiagramSymbolError) {
        blocked.push({ feature_id: fid, blocked_reason: exc.code, detail: exc.message });
        continue;
      }
      throw exc;
    }
    const src = anchorRes.anchor_source;
    anchorCounts[src] = (anchorCounts[src] ?? 0) + 1;
    const row = values[idx];
    const importance = fsum(row.map(([, v]) => Math.abs(v)));
    candidates.push({
      feature_id: fid,
      wx,
      wy,
      anchor: [lng, lat],
      anchor_source: src,
      radius_px: boundRadii[idx],
      importance,
    });
  }

  // 2) 确定性优先级：importance 降序，平局 feature_id 字典序升序
  candidates.sort(
    (a, b) =>
      b.importance - a.importance ||
      (a.feature_id < b.feature_id ? -1 : a.feature_id > b.feature_id ? 1 : 0),
  );

  // 3) 贪心放置（参考缩放像素平面）
  const scale = worldPxScale(policy.reference_zoom);
  const padding = policy.collision_padding_px;
  const placedBoxes: [number, number, number][] = []; // (px, py, r_eff)
  const glyphs: LayoutGlyph[] = [];
  let omittedCollision = 0;
  let omittedBudget = 0;
  let omittedSmall = 0;
  let displacedCount = 0;
  let leaderCount = 0;

  const collides = (px: number, py: number, r: number): boolean => {
    for (const [ox, oy, orr] of placedBoxes) {
      const rr = r + orr + padding;
      if ((px - ox) * (px - ox) + (py - oy) * (py - oy) < rr * rr) return true;
    }
    return false;
  };

  for (const cand of candidates) {
    const r = cand.radius_px;
    if (r < policy.hide_below_radius_px) {
      omittedSmall += 1;
      continue;
    }
    if (glyphs.length >= policy.max_glyphs) {
      omittedBudget += 1;
      continue;
    }
    const bx = cand.wx * scale;
    const by = cand.wy * scale;
    let positionPx: [number, number] = [bx, by];
    let displacedPx = 0.0;
    if (collides(bx, by, r)) {
      let placedHere = false;
      if (policy.displacement_enabled) {
        const step = (r + padding) / 2.0;
        for (let stepIdx = 1; stepIdx <= MAX_DISPLACEMENT_STEPS; stepIdx++) {
          const dist = step * stepIdx;
          for (const [dx, dy] of NUDGE_DIRECTIONS) {
            const nx = bx + dx * dist;
            const ny = by + dy * dist;
            if (!collides(nx, ny, r)) {
              positionPx = [nx, ny];
              displacedPx = dist;
              placedHere = true;
              break;
            }
          }
          if (placedHere) break;
        }
      }
      if (!placedHere) {
        omittedCollision += 1;
        continue;
      }
    }
    placedBoxes.push([positionPx[0], positionPx[1], r]);
    const [posLng, posLat] = worldToLnglat(positionPx[0] / scale, positionPx[1] / scale);
    const leader = displacedPx > policy.leader_threshold_px;
    if (displacedPx > 0) displacedCount += 1;
    if (leader) leaderCount += 1;
    glyphs.push({
      feature_id: cand.feature_id,
      anchor: [cand.anchor[0], cand.anchor[1]],
      anchor_source: cand.anchor_source,
      position: [round9(posLng), round9(posLat)],
      displacement_px: quantize3(displacedPx),
      leader_line: leader,
      radius_px: quantize3(r),
      min_zoom: glyphMinZoom(r, policy.min_zoom_floor),
      priority_rank: 0, // 输出序赋值（贪心放置序 = 重要性序）
    });
  }

  for (let rank = 0; rank < glyphs.length; rank++) {
    glyphs[rank].priority_rank = rank;
  }

  const evidence: LayoutEvidence = {
    candidates: candidates.length,
    placed: glyphs.length,
    omitted_collision: omittedCollision,
    omitted_budget: omittedBudget,
    omitted_small: omittedSmall,
    blocked: blocked.length,
    blocked_detail: blocked.slice(0, 64), // 有界明细（防响应放大）
    blocked_truncated: blocked.length > 64,
    displaced: displacedCount,
    leader_lines: leaderCount,
    anchor_source_counts: anchorCounts,
    features_total: features.length,
    policy,
  };
  return {
    schema: "diagram_symbol_layout.v1",
    glyphs,
    evidence,
    digest: layoutDigest(glyphs),
  };
}
