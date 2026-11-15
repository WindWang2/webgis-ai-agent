/**
 * G40 规范形资产 → MapLibre 注册图像（ImageData）光栅化。
 *
 * 语义与 Python 端 symbol_asset_svg.py / svg_import.py 投影一致：
 * - 点符号：viewBox 域图元按 size_px 目标缩放光栅化（pixelRatio=2）；
 * - 面纹理：与 SVG `<pattern patternTransform="rotate(θ)">`（水平线族 +
 *   旋转）同一几何 —— tile 内水平线族，pattern 旋转角 θ（与导出
 *   patternTransform 语义逐像素同源）；
 * - 背景：bg_color transparent → alpha 0（不混入未知底，S13）。
 *
 * 运行环境：浏览器 Canvas2D/Path2D（Node 单测走几何 oracle，不做像素
 * 全等 —— 像素级一致性由导出链 rsvg/WeasyPrint 实证承担）。
 */

import {
  PatternType,
  PointParams,
  SymbolAssetDocument,
  SymbolPrimitive,
  TextureParams,
} from './contracts';

export const TEXTURE_PIXEL_RATIO = 2;
const PATTERN_TILE_SPAN = 256; // pattern tile 边长上限（px，S25 预算）

function ctxOf(w: number, h: number): CanvasRenderingContext2D {
  if (typeof document === 'undefined') {
    throw new Error('CanvasRenderingContext2D unavailable (non-browser env)');
  }
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(w));
  canvas.height = Math.max(1, Math.round(h));
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new Error('canvas 2d context unavailable');
  return ctx;
}

function pathOfPrim(ctx: CanvasRenderingContext2D, p: SymbolPrimitive): void {
  ctx.beginPath();
  if (p.op === 'path' && p.d) {
    for (const cmd of p.d) {
      const c = String(cmd[0]);
      const n = cmd.slice(1).map(Number);
      if (c === 'M') ctx.moveTo(n[0], n[1]);
      else if (c === 'L') ctx.lineTo(n[0], n[1]);
      else if (c === 'C') ctx.bezierCurveTo(n[0], n[1], n[2], n[3], n[4], n[5]);
      else if (c === 'Z') ctx.closePath();
    }
  } else if (p.op === 'circle') {
    ctx.moveTo((p.cx ?? 0) + (p.r ?? 0), p.cy ?? 0);
    ctx.arc(p.cx ?? 0, p.cy ?? 0, p.r ?? 0, 0, Math.PI * 2);
  } else if (p.op === 'ellipse') {
    ctx.ellipse(p.cx ?? 0, p.cy ?? 0, p.rx ?? 0, p.ry ?? 0, 0, 0, Math.PI * 2);
  } else if (p.op === 'rect') {
    ctx.rect(p.x ?? 0, p.y ?? 0, p.w ?? 0, p.h ?? 0);
  } else if (p.op === 'line') {
    ctx.moveTo(p.x ?? 0, p.y ?? 0);
    ctx.lineTo((p.x ?? 0) + (p.w ?? 0), (p.y ?? 0) + (p.h ?? 0));
  } else if (p.op === 'polygon' || p.op === 'polyline') {
    const pts = p.points ?? [];
    if (pts.length) {
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      if (p.op === 'polygon') ctx.closePath();
    }
  }
}

function paintPrim(ctx: CanvasRenderingContext2D, p: SymbolPrimitive): void {
  pathOfPrim(ctx, p);
  const opacity = p.opacity ?? 1;
  ctx.globalAlpha = opacity;
  const fill = p.fill ?? 'none';
  const stroke = p.stroke ?? 'none';
  if (fill !== 'none' && fill !== 'transparent') {
    ctx.fillStyle = fill;
    ctx.fill();
  }
  if (stroke !== 'none' && stroke !== 'transparent' && (p.stroke_width ?? 0) > 0) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = p.stroke_width ?? 1;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

/** 点符号 → ImageData（外接框 = size_px × size_px × ratio；纵横比保持）。 */
export function rasterizePointSymbol(
  doc: SymbolAssetDocument,
  params: PointParams,
  pixelRatio: number = TEXTURE_PIXEL_RATIO,
): ImageData {
  const [vw, vh] = doc.viewbox ?? [24, 24];
  const s = (params.size_px * pixelRatio) / Math.max(vw, vh);
  const w = Math.max(1, Math.ceil(vw * s));
  const h = Math.max(1, Math.ceil(vh * s));
  const ctx = ctxOf(w, h);
  ctx.scale(s, s);
  for (const p of doc.primitives ?? []) {
    if (params.color) {
      paintPrim(ctx, { ...p, fill: p.fill && p.fill !== 'none' ? params.color : p.fill });
    } else {
      paintPrim(ctx, p);
    }
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = params.opacity;
  const img = ctx.getImageData(0, 0, w, h);
  if (params.opacity < 1) {
    for (let i = 3; i < img.data.length; i += 4) {
      img.data[i] = Math.round(img.data[i] * params.opacity);
    }
  }
  return img;
}

/**
 * 面纹理 → ImageData tile（MapLibre fill-pattern 以 tile 周期平铺）。
 *
 * F3（评审）：与 SVG `patternTransform="rotate(θ)"` 的几何**逐间距等价**
 * —— 旋转网格语义（线族方向 (q,p)/hyp、垂直间距恒 = spacing），而非
 * "tile 预旋转后轴对齐平铺"（后者 45° 时有效间距只有 0.707×spacing）。
 * 无缝平铺的格点解：方向取有理斜率 tanθ_eff = p/q（连分数逼近，误差
 * ≤0.3°），tile 边长 S = d_eff·√(p²+q²)，d_eff = S/√(p²+q²) ≈ d（亚像素
 * 量化误差）—— x/y 平移 S 各使相位推进 q·d_eff / p·d_eff（整数倍）→
 * 拼缝严格连续。
 */
export function rasterizeAreaTexture(
  doc: SymbolAssetDocument,
  params: TextureParams,
  pixelRatio: number = TEXTURE_PIXEL_RATIO,
): ImageData {
  const type: PatternType = doc.pattern?.type ?? 'hatch';
  const stroke = params.stroke_width_px * pixelRatio;

  if (type === 'svg_tile') {
    // F9（评审）：tile 周期 = tile_viewbox × pattern_scale × ratio（与导出
    // pattern 同式）；tile 图元保留自身颜色（fg_color 不覆涂）。
    const [tw, th] = doc.pattern?.tile_viewbox ?? [12, 12];
    const w = Math.max(4, Math.min(PATTERN_TILE_SPAN,
      Math.round(tw * params.pattern_scale * pixelRatio)));
    const h = Math.max(4, Math.min(PATTERN_TILE_SPAN,
      Math.round(th * params.pattern_scale * pixelRatio)));
    const ctx = ctxOf(w, h);
    if (params.bg_color !== 'transparent') {
      ctx.fillStyle = params.bg_color;
      ctx.fillRect(0, 0, w, h);
    }
    ctx.scale(w / tw, h / th);
    for (const p of doc.pattern?.tile_primitives ?? []) paintPrim(ctx, p);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    return ctx.getImageData(0, 0, w, h);
  }

  if (type === 'dots') {
    const spacing = Math.min(PATTERN_TILE_SPAN,
      Math.max(4, Math.round(params.spacing_px * params.pattern_scale * pixelRatio)));
    const size = spacing;
    const ctx = ctxOf(size, size);
    if (params.bg_color !== 'transparent') {
      ctx.fillStyle = params.bg_color;
      ctx.fillRect(0, 0, size, size);
    }
    ctx.fillStyle = params.fg_color;
    const r = Math.max(1, stroke / 2);
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, r, 0, Math.PI * 2);
    ctx.fill();
    return ctx.getImageData(0, 0, size, size);
  }

  // hatch / cross_hatch：有理角格点（θ_eff = atan(p/q)，无缝平铺严格解）
  const d = params.spacing_px * params.pattern_scale * pixelRatio;
  const theta = Math.max(-90, Math.min(90, params.angle_deg));
  const { p, q, size } = rationalLineLattice(theta, d);
  const hyp = Math.hypot(p, q);
  const dEff = size / hyp;
  const ctx = ctxOf(size, size);
  if (params.bg_color !== 'transparent') {
    ctx.fillStyle = params.bg_color;
    ctx.fillRect(0, 0, size, size);
  }
  ctx.strokeStyle = params.fg_color;
  ctx.lineWidth = Math.max(1, stroke);
  ctx.lineCap = 'butt';
  // 方向 dir = (q,p)/hyp；法向 nrm = (-p,q)/hyp。线族：过 center + k·dEff·nrm。
  const cx = size / 2;
  const cy = size / 2;
  const diag = size * 1.5;
  const families: Array<[number, number]> = [[q / hyp, p / hyp]];
  if (type === 'cross_hatch') families.push([-p / hyp, q / hyp]);
  ctx.beginPath();
  const kSpan = Math.ceil(diag / dEff) + 1;
  for (const [dx, dy] of families) {
    const nx = -dy;
    const ny = dx;
    for (let k = -kSpan; k <= kSpan; k++) {
      const px = cx + nx * k * dEff;
      const py = cy + ny * k * dEff;
      ctx.moveTo(px - dx * diag / 2, py - dy * diag / 2);
      ctx.lineTo(px + dx * diag / 2, py + dy * diag / 2);
    }
  }
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

/**
 * 有理斜率格点选择：在 tile 预算内求 (p, q, S)，使 atan2(p,q) 最逼近 θ、
 * S = round(d·√(p²+q²)) ∈ [8, 256]、d_eff = S/√(p²+q²) ≈ d。
 * θ∈[-90,90]：p 可为负（镜像）；θ=±90 → (±1,0)；θ=0 → (0,1)。
 */
export function rationalLineLattice(
  thetaDeg: number, d: number,
): { p: number; q: number; size: number } {
  const rad = (thetaDeg * Math.PI) / 180;
  const tan = Math.tan(rad);
  let best: { p: number; q: number; size: number; err: number } | null = null;
  const cap = Math.max(8, Math.min(PATTERN_TILE_SPAN, Math.round(d * 40)));
  const minSize = Math.max(8, Math.min(PATTERN_TILE_SPAN, Math.round(d)));
  for (let q = 0; q <= 24; q++) {
    for (let p = -24; p <= 24; p++) {
      if (q === 0 && Math.abs(p) !== 1) continue; // ±90° 只有 (±1,0)
      const hyp = Math.hypot(p, q);
      if (hyp === 0) continue;
      const size = Math.round(d * hyp);
      if (size < minSize || size > cap) continue;
      const err = Math.abs(Math.atan2(p, q) - rad);
      if (!best || err < best.err - 1e-12) {
        best = { p, q, size, err };
      }
    }
  }
  if (best) return { p: best.p, q: best.q, size: best.size };
  // 兜底：正交线族（θ≈0/90 或预算极端）—— 恒无缝。
  if (Math.abs(tan) > 1) return { p: Math.sign(tan), q: 0, size: Math.max(8, Math.round(d)) };
  return { p: 0, q: 1, size: Math.max(8, Math.round(d)) };
}

/** swatch SVG 字符串（屏上图例；与后端 swatch_svg 同语义的 TS 投影）。 */
export function swatchSvgString(
  doc: SymbolAssetDocument,
  params: Record<string, unknown>,
  boxW: number,
  boxH: number,
): string {
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const parts: string[] = [];
  if (doc.kind === 'point_symbol') {
    const [vw, vh] = doc.viewbox ?? [24, 24];
    const s = Math.min(boxW / vw, boxH / vh);
    const ox = (boxW - vw * s) / 2;
    const oy = (boxH - vh * s) / 2;
    parts.push(
      `<g transform="translate(${ox.toFixed(2)} ${oy.toFixed(2)}) scale(${s.toFixed(4)})">` +
      primSvg(doc.primitives ?? []) + '</g>');
  } else if (doc.pattern?.type === 'svg_tile') {
    // F9（评审）：导入 tile 的 swatch 画 tile 本体（保留自身颜色）。
    const [tw, th] = doc.pattern.tile_viewbox ?? [12, 12];
    const s = Math.min(boxW / tw, boxH / th);
    const ox = (boxW - tw * s) / 2;
    const oy = (boxH - th * s) / 2;
    parts.push(
      `<g transform="translate(${ox.toFixed(2)} ${oy.toFixed(2)}) scale(${s.toFixed(4)})">` +
      primSvg(doc.pattern.tile_primitives ?? []) + '</g>');
  } else {
    const p = params as unknown as TextureParams;
    if (p?.bg_color && p.bg_color !== 'transparent') {
      parts.push(`<rect width="${boxW}" height="${boxH}" fill="${esc(p.bg_color)}"/>`);
    }
    // 密度按盒尺寸可读化重投（同后端 swatch 语义）
    const angle = Number(p?.angle_deg ?? 45);
    const spacing = Math.max(3, Math.min(boxW / 3, Number(p?.spacing_px ?? 10)));
    const stroke = Math.max(0.6, Number(p?.stroke_width_px ?? 1.5));
    const type = doc.pattern?.type ?? 'hatch';
    if (type === 'dots') {
      const r = Math.max(0.8, stroke / 2);
      const n = Math.max(2, Math.floor(boxW / spacing));
      const sp = boxW / n;
      for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
          parts.push(`<circle cx="${(sp * (i + 0.5)).toFixed(2)}" cy="${(sp * (j + 0.5)).toFixed(2)}" r="${r.toFixed(2)}" fill="${esc(p?.fg_color ?? '#334155')}"/>`);
        }
      }
    } else {
      const rad = (angle * Math.PI) / 180;
      const nx = -Math.sin(rad);
      const ny = Math.cos(rad);
      const diag = Math.hypot(boxW, boxH);
      const cx = boxW / 2;
      const cy = boxH / 2;
      const dx = Math.cos(rad);
      const dy = Math.sin(rad);
      const count = Math.ceil(diag / spacing) + 2;
      for (let i = -count; i <= count; i++) {
        const px = cx + nx * i * spacing;
        const py = cy + ny * i * spacing;
        const line = (dxx: number, dyy: number) =>
          `<line x1="${(px - dxx * diag / 2).toFixed(2)}" y1="${(py - dyy * diag / 2).toFixed(2)}" x2="${(px + dxx * diag / 2).toFixed(2)}" y2="${(py + dyy * diag / 2).toFixed(2)}" stroke="${esc(p?.fg_color ?? '#334155')}" stroke-width="${stroke.toFixed(2)}"/>`;
        parts.push(line(dx, dy));
        if (type === 'cross_hatch') parts.push(line(-dy, dx));
      }
    }
  }
  return `<g class="cartoasset-swatch">${parts.join('')}</g>`;
}

function primSvg(prims: SymbolPrimitive[]): string {
  const out: string[] = [];
  for (const p of prims) {
    const attrs: string[] = [`fill="${p.fill ?? 'none'}"`];
    if ((p.opacity ?? 1) < 1) attrs.push(`fill-opacity="${p.opacity}"`);
    if (p.stroke && p.stroke !== 'none') {
      attrs.push(`stroke="${p.stroke}" stroke-width="${p.stroke_width ?? 1}"`);
    }
    if (p.op === 'path' && p.d) {
      out.push(`<path d="${p.d.map((c) => c.join(' ')).join(' ')}" ${attrs.join(' ')}/>`);
    } else if (p.op === 'circle') {
      out.push(`<circle cx="${p.cx}" cy="${p.cy}" r="${p.r}" ${attrs.join(' ')}/>`);
    } else if (p.op === 'rect') {
      out.push(`<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" ${attrs.join(' ')}/>`);
    } else if (p.op === 'polygon' || p.op === 'polyline') {
      out.push(`<${p.op} points="${(p.points ?? []).map((q) => q.join(',')).join(' ')}" ${attrs.join(' ')}/>`);
    } else if (p.op === 'line') {
      out.push(`<line x1="${p.x}" y1="${p.y}" x2="${(p.x ?? 0) + (p.w ?? 0)}" y2="${(p.y ?? 0) + (p.h ?? 0)}" stroke="${p.stroke}" stroke-width="${p.stroke_width ?? 1}"/>`);
    }
  }
  return out.join('');
}
