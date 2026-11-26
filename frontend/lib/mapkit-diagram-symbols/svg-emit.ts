/**
 * svg-emit.ts —— 图符 → SVG path 片段（前端 vector SVG 导出 twin 分支）。
 *
 * 与后端 mapspec_to_svg.py 的 diagram 分支同语义（几何都来自各自的位级
 * 孪生 oracle，故 path 数值逐位一致；角度投影同式 x=cx+r·sin、y=cy−r·cos，
 * 0°=12 点钟顺时针；条形 y=cy−y 翻折；full-circle 双半圆弧防弧指令退化）。
 * 输出为可直接拼入 SVG body 的 `<g>` 片段；全部动态文本经 xml 转义。
 */
import {
  buildGlyphGeometry,
  computeGlyphScale,
  glyphBoundRadius,
  normalizeSizePolicy,
  type DiagramGlyph,
  type SizePolicyInput,
} from "./oracle";
import { qualifyFeatureValues, resolvePaletteColors } from "./integration";
import { solveDiagramSymbolLayout, type LayoutEvidence } from "./layout";

function fmt(v: number): string {
  const s = String(Math.round(v * 1000) / 1000);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&apos;");
}

/** 角度（0°=12 点钟顺时针）→ 单位向量（SVG y-down）。 */
function angleUnit(deg: number): [number, number] {
  const rad = (deg * Math.PI) / 180;
  return [Math.sin(rad), -Math.cos(rad)];
}

function slicePath(cx: number, cy: number, r: number,
                   startDeg: number, endDeg: number): string {
  const span = endDeg - startDeg;
  if (span <= 0) return "";
  const [sx, sy] = angleUnit(startDeg);
  const [ex, ey] = angleUnit(endDeg);
  const x0 = cx + r * sx, y0 = cy + r * sy;
  const x1 = cx + r * ex, y1 = cy + r * ey;
  const large = span > 180 ? 1 : 0;
  if (span >= 359.999) {
    // full circle：双半圆弧（弧指令对 360° 退化为零长）
    const [mx, my] = angleUnit(startDeg + 180);
    return `M ${fmt(cx)} ${fmt(cy)} L ${fmt(x0)} ${fmt(y0)} `
      + `A ${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(cx + r * mx)} ${fmt(cy + r * my)} `
      + `A ${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(x0)} ${fmt(y0)} Z`;
  }
  return `M ${fmt(cx)} ${fmt(cy)} L ${fmt(x0)} ${fmt(y0)} `
    + `A ${fmt(r)} ${fmt(r)} 0 ${large} 1 ${fmt(x1)} ${fmt(y1)} Z`;
}

function donutSlicePath(cx: number, cy: number, rOuter: number, rInner: number,
                        startDeg: number, endDeg: number): string {
  const span = endDeg - startDeg;
  if (span <= 0) return "";
  const [sx, sy] = angleUnit(startDeg);
  const [ex, ey] = angleUnit(endDeg);
  const large = span > 180 ? 1 : 0;
  return `M ${fmt(cx + rOuter * sx)} ${fmt(cy + rOuter * sy)} `
    + `A ${fmt(rOuter)} ${fmt(rOuter)} 0 ${large} 1 ${fmt(cx + rOuter * ex)} ${fmt(cy + rOuter * ey)} `
    + `L ${fmt(cx + rInner * ex)} ${fmt(cy + rInner * ey)} `
    + `A ${fmt(rInner)} ${fmt(rInner)} 0 ${large} 0 ${fmt(cx + rInner * sx)} ${fmt(cy + rInner * sy)} Z`;
}

export interface DiagramSvgProject {
  (coord: [number, number]): [number, number];
}

/** 层 diagram_symbol 块 + 源要素 → SVG `<g>` 片段（文档序=叠在所有层之上）。 */
export function emitDiagramSymbolLayerSvg(
  block: Record<string, unknown>,
  features: Array<Record<string, unknown>>,
  project: DiagramSvgProject,
): { svg: string; placed: number; evidence: LayoutEvidence } | { error: string } {
  try {
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

    const parts: string[] = [];
    const ev = plan.evidence;
    const evAttrs =
      ` data-placed="${String(ev.placed ?? 0)}"`
      + ` data-omitted-collision="${String(ev.omitted_collision ?? 0)}"`
      + ` data-omitted-budget="${String(ev.omitted_budget ?? 0)}"`
      + ` data-blocked="${String(ev.blocked ?? 0)}"`;
    parts.push(`<g data-diagram-symbol-layer="1" data-kind="${escapeXml(kind)}"${evAttrs}>`);
    for (const glyph of plan.glyphs) {
      const [cxS, cyS] = project(glyph.position as [number, number]);
      const cx = Number(cxS), cy = Number(cyS);
      const geo = geometryById.get(glyph.feature_id);
      if (!geo) continue;
      const attrs =
        ` data-feature-id="${escapeXml(glyph.feature_id)}"`
        + ` data-priority-rank="${glyph.priority_rank}"`;
      let body = "";
      if (glyph.leader_line) {
        const [ax, ay] = project(glyph.anchor as [number, number]);
        body += `<line x1="${fmt(Number(ax))}" y1="${fmt(Number(ay))}" x2="${fmt(cx)}" y2="${fmt(cy)}" stroke="#334155" stroke-width="1" />\n`;
      }
      if (geo.kind === "composition_pie" || geo.kind === "composition_donut") {
        const inner = geo.inner_radius_px ?? 0;
        for (const slice of geo.slices) {
          const color = palette[categories.indexOf(slice.category) % palette.length];
          const d = inner > 0
            ? donutSlicePath(cx, cy, geo.radius_px, inner, slice.start_angle_deg, slice.end_angle_deg)
            : slicePath(cx, cy, geo.radius_px, slice.start_angle_deg, slice.end_angle_deg);
          if (d) body += `<path d="${d}" fill="${color}" />\n`;
        }
      } else if (geo.kind === "series_bars") {
        for (const seg of geo.segments) {
          if (seg.value === 0) continue;
          const color = palette[categories.indexOf(seg.category) % palette.length];
          const x = Math.min(seg.x0, seg.x1);
          const w = Math.abs(seg.x1 - seg.x0);
          const top = Math.max(seg.y0, seg.y1);
          const h = Math.abs(seg.y1 - seg.y0);
          body += `<rect x="${fmt(cx + x)}" y="${fmt(cy - top)}" width="${fmt(w)}" height="${fmt(h)}" fill="${color}" />\n`;
        }
      } else if (geo.kind === "segmented_proportional") {
        for (const seg of geo.segments) {
          if (seg.radius_px <= 0) continue;
          const color = palette[categories.indexOf(seg.category) % palette.length];
          body += `<path d="${slicePath(cx, cy, seg.radius_px, seg.start_angle_deg, seg.end_angle_deg)}" fill="${color}" />\n`;
        }
      }
      parts.push(`<g data-diagram-symbol="1"${attrs}>\n${body}</g>\n`);
    }
    parts.push("</g>\n");
    return {
      svg: parts.join(""),
      placed: plan.glyphs.length,
      evidence: plan.evidence,
    };
  } catch (err) {
    // oracle typed 拒绝 → 有界披露（调用方 onDiagnostic 通道），不抛穿导出。
    return { error: String(err instanceof Error ? err.message : err).slice(0, 160) };
  }
}
