/**
 * vertices.ts —— 图符几何 → GL 三角形顶点的纯函数（无 GL 依赖，可单测）。
 *
 * 坐标约定：输出为 glyph-local 屏幕 px 偏移（**y 轴向下**，与 canvas/GL
 * 屏幕空间同向）。oracle 的 glyph-local 坐标是 y 轴向上（基线 y=0，正值
 * 向上）——条形段在此翻转：screen_y = -local_y。
 * 角度约定：0° = 12 点钟方向，顺时针为正（屏幕 y 向下空间中方向向量为
 * (sin θ, -cos θ)：θ=0 → 上，θ=90° → 右）。
 *
 * 拆分规则（与 layer.ts 的使命约定冻结）：
 * - pie slice：中心 → 弧的三角形扇（arc segments = clamp(ceil(span/6), 2, 48)）；
 * - donut ring：外弧/内弧间的 triangle strip（展开为 2·segments 个三角形）；
 * - bar segment：2 个三角形（quad）；
 * - segmented wedge：每段按其 radius_px 的中心扇。
 * 颜色：hex → RGB 归一 0..1，逐顶点输出（palette 按类别序取模循环）。
 */

import type { CompositionGlyph, DiagramGlyph, SegmentedGlyph, SeriesBarsGlyph } from "./oracle";

export interface RgbaColor {
  r: number;
  g: number;
  b: number;
  a: number;
}

export interface TriangulatedGlyph {
  /**
   * 交错顶点 [x, y, r, g, b, a] × vertexCount；
   * (x, y) 为 glyph-local 屏幕 px（y 向下），(r,g,b,a) 归一 0..1。
   */
  vertices: Float32Array;
  vertexCount: number;
}

/** hex（#rgb / #rrggbb）→ RGB 归一 0..1；非法输入抛 Error。 */
export function parseHexColor(hex: string): RgbaColor {
  let s = hex.trim();
  if (s.startsWith("#")) s = s.slice(1);
  if (/^[0-9a-fA-F]{3}$/.test(s)) {
    const r = parseInt(s[0] + s[0], 16) / 255;
    const g = parseInt(s[1] + s[1], 16) / 255;
    const b = parseInt(s[2] + s[2], 16) / 255;
    return { r, g, b, a: 1 };
  }
  if (/^[0-9a-fA-F]{6}$/.test(s)) {
    const r = parseInt(s.slice(0, 2), 16) / 255;
    const g = parseInt(s.slice(2, 4), 16) / 255;
    const b = parseInt(s.slice(4, 6), 16) / 255;
    return { r, g, b, a: 1 };
  }
  throw new Error(`invalid hex color: ${JSON.stringify(hex)}（支持 #rgb / #rrggbb）`);
}

/** 0° = 12 点钟、顺时针为正 → 屏幕 y 向下空间的单位方向向量。 */
export function glyphAngleUnitVector(angleDeg: number): [number, number] {
  const rad = (angleDeg * Math.PI) / 180.0;
  return [Math.sin(rad), -Math.cos(rad)];
}

/** arc segments = clamp(ceil(spanDeg/6), 2, 48)。 */
export function arcSegmentCount(spanDeg: number): number {
  return Math.min(48, Math.max(2, Math.ceil(spanDeg / 6)));
}

function paletteColorAt(palette: readonly string[], index: number): RgbaColor {
  if (palette.length === 0) {
    throw new Error("palette must not be empty（图例与几何同源：色槽来自 palette）");
  }
  return parseHexColor(palette[index % palette.length]);
}

/** pie slice：中心 → 弧三角形扇（顶点数 = segments × 3）。 */
export function pieSliceFan(
  radiusPx: number,
  startDeg: number,
  endDeg: number,
  color: RgbaColor,
): TriangulatedGlyph {
  const span = endDeg - startDeg;
  const segs = arcSegmentCount(span);
  const vertexCount = segs * 3;
  const out = new Float32Array(vertexCount * 6);
  let o = 0;
  for (let i = 0; i < segs; i++) {
    const a0 = startDeg + (span * i) / segs;
    const a1 = startDeg + (span * (i + 1)) / segs;
    const [x0, y0] = glyphAngleUnitVector(a0);
    const [x1, y1] = glyphAngleUnitVector(a1);
    // (center, arc0, arc1)
    const tri: readonly (readonly [number, number])[] = [
      [0, 0],
      [x0 * radiusPx, y0 * radiusPx],
      [x1 * radiusPx, y1 * radiusPx],
    ];
    for (const [vx, vy] of tri) {
      out[o] = vx; out[o + 1] = vy;
      out[o + 2] = color.r; out[o + 3] = color.g; out[o + 4] = color.b; out[o + 5] = color.a;
      o += 6;
    }
  }
  return { vertices: out, vertexCount };
}

/** donut ring segment：外/内弧间的 strip，展开为 2·segments 个三角形。 */
export function donutRingStrip(
  outerRadiusPx: number,
  innerRadiusPx: number,
  startDeg: number,
  endDeg: number,
  color: RgbaColor,
): TriangulatedGlyph {
  const span = endDeg - startDeg;
  const segs = arcSegmentCount(span);
  const vertexCount = segs * 6;
  const out = new Float32Array(vertexCount * 6);
  let o = 0;
  for (let i = 0; i < segs; i++) {
    const a0 = startDeg + (span * i) / segs;
    const a1 = startDeg + (span * (i + 1)) / segs;
    const [ux0, uy0] = glyphAngleUnitVector(a0);
    const [ux1, uy1] = glyphAngleUnitVector(a1);
    const o0x = ux0 * outerRadiusPx, o0y = uy0 * outerRadiusPx;
    const o1x = ux1 * outerRadiusPx, o1y = uy1 * outerRadiusPx;
    const i0x = ux0 * innerRadiusPx, i0y = uy0 * innerRadiusPx;
    const i1x = ux1 * innerRadiusPx, i1y = uy1 * innerRadiusPx;
    // strip 段展开：tri(o0, o1, i1) + tri(o0, i1, i0)
    const tris: readonly (readonly [number, number])[][] = [
      [[o0x, o0y], [o1x, o1y], [i1x, i1y]],
      [[o0x, o0y], [i1x, i1y], [i0x, i0y]],
    ];
    for (const tri of tris) {
      for (const [vx, vy] of tri) {
        out[o] = vx; out[o + 1] = vy;
        out[o + 2] = color.r; out[o + 3] = color.g; out[o + 4] = color.b; out[o + 5] = color.a;
        o += 6;
      }
    }
  }
  return { vertices: out, vertexCount };
}

/**
 * bar segment：2 个三角形 quad。glyph-local y 轴向上在此翻转为屏幕
 * y 向下：screen_y = -local_y（正值段渲染在基线上方 = 屏幕负 y 方向）。
 */
export function barQuad(
  x0Px: number,
  x1Px: number,
  y0LocalPx: number,
  y1LocalPx: number,
  color: RgbaColor,
): TriangulatedGlyph {
  const sy0 = -y0LocalPx;
  const sy1 = -y1LocalPx;
  const tris: readonly (readonly [number, number])[][] = [
    [[x0Px, sy0], [x1Px, sy0], [x1Px, sy1]],
    [[x0Px, sy0], [x1Px, sy1], [x0Px, sy1]],
  ];
  const vertexCount = 6;
  const out = new Float32Array(vertexCount * 6);
  let o = 0;
  for (const tri of tris) {
    for (const [vx, vy] of tri) {
      out[o] = vx; out[o + 1] = vy;
      out[o + 2] = color.r; out[o + 3] = color.g; out[o + 4] = color.b; out[o + 5] = color.a;
      o += 6;
    }
  }
  return { vertices: out, vertexCount };
}

function concat(parts: TriangulatedGlyph[]): TriangulatedGlyph {
  const vertexCount = parts.reduce((acc, p) => acc + p.vertexCount, 0);
  const out = new Float32Array(vertexCount * 6);
  let o = 0;
  for (const p of parts) {
    out.set(p.vertices, o);
    o += p.vertices.length;
  }
  return { vertices: out, vertexCount };
}

function triangulateComposition(g: CompositionGlyph, palette: readonly string[]): TriangulatedGlyph {
  const parts: TriangulatedGlyph[] = [];
  const inner =
    g.inner_radius_px !== undefined
      ? g.inner_radius_px
      : g.inner_radius_ratio !== undefined
        ? g.radius_px * g.inner_radius_ratio
        : 0;
  for (let i = 0; i < g.slices.length; i++) {
    const s = g.slices[i];
    const color = paletteColorAt(palette, i);
    if (inner > 0) {
      parts.push(donutRingStrip(g.radius_px, inner, s.start_angle_deg, s.end_angle_deg, color));
    } else {
      parts.push(pieSliceFan(g.radius_px, s.start_angle_deg, s.end_angle_deg, color));
    }
  }
  return concat(parts);
}

function triangulateBars(g: SeriesBarsGlyph, palette: readonly string[]): TriangulatedGlyph {
  const parts: TriangulatedGlyph[] = [];
  for (let i = 0; i < g.segments.length; i++) {
    const s = g.segments[i];
    if (s.y0 === s.y1) continue; // 零值段不可见（有 zero_values 会计）
    parts.push(barQuad(s.x0, s.x1, s.y0, s.y1, paletteColorAt(palette, i)));
  }
  return concat(parts);
}

function triangulateSegmented(g: SegmentedGlyph, palette: readonly string[]): TriangulatedGlyph {
  const parts: TriangulatedGlyph[] = [];
  for (let i = 0; i < g.segments.length; i++) {
    const s = g.segments[i];
    if (s.radius_px <= 0) continue; // 零值段不可见（有 zero_segments 会计）
    parts.push(pieSliceFan(s.radius_px, s.start_angle_deg, s.end_angle_deg, paletteColorAt(palette, i)));
  }
  return concat(parts);
}

/** 按 kind 分派的三角化入口（layout.render 消费）。 */
export function triangulateGlyph(glyph: DiagramGlyph, palette: readonly string[]): TriangulatedGlyph {
  switch (glyph.kind) {
    case "composition_pie":
    case "composition_donut":
      return triangulateComposition(glyph, palette);
    case "series_bars":
      return triangulateBars(glyph, palette);
    case "segmented_proportional":
      return triangulateSegmented(glyph, palette);
  }
}
