/**
 * G40 制图符号资产前端契约（与 app/lib/cartography/symbol_asset.py 对齐）。
 *
 * 纪律：TS 端只做**参数归一镜像**（bounds/词表与 Python 端逐字段一致，
 * contracts.test.ts 锁定）；规范形文档由后端单一清洗口径产出，前端消费
 * 不再二次解析原始 SVG。资产内容经授权 API 拉取（scope 在服务端）。
 */

// ─── 参数上下限（与 symbol_asset.py 同值；勿单边漂移） ────────────────────

export const POINT_SIZE_PX_MIN = 1.0;
export const POINT_SIZE_PX_MAX = 128.0;
export const TEXTURE_SPACING_MIN = 4.0;
export const TEXTURE_SPACING_MAX = 64.0;
export const TEXTURE_STROKE_MIN = 0.25;
export const TEXTURE_STROKE_MAX = 8.0;
export const TEXTURE_SCALE_MIN = 0.25;
export const TEXTURE_SCALE_MAX = 4.0;
export const ROTATION_MIN = -360.0;
export const ROTATION_MAX = 360.0;

export const ANCHORS = [
  'center', 'left', 'right', 'top', 'bottom',
  'top-left', 'top-right', 'bottom-left', 'bottom-right',
] as const;

export type PointAnchor = (typeof ANCHORS)[number];
export type AssetKind = 'point_symbol' | 'area_texture';
export type PatternType = 'hatch' | 'cross_hatch' | 'dots' | 'svg_tile';

export interface PointParams {
  size_px: number;
  anchor: PointAnchor;
  rotation_deg: number;
  opacity: number;
  color?: string;
}

export interface TextureParams {
  spacing_px: number;
  angle_deg: number;
  stroke_width_px: number;
  pattern_scale: number;
  fg_color: string;
  bg_color: string; // hex 或 'transparent'
}

// ─── 规范形文档（有界子集；后端 SymbolAssetDocument 投影） ────────────────

export interface SymbolPrimitive {
  op: 'path' | 'circle' | 'rect' | 'ellipse' | 'line' | 'polygon' | 'polyline';
  fill?: string;
  stroke?: string;
  stroke_width?: number;
  opacity?: number;
  d?: (string | number)[][];
  cx?: number;
  cy?: number;
  r?: number;
  rx?: number;
  ry?: number;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  points?: [number, number][];
}

export interface TexturePattern {
  type: PatternType;
  tile_primitives?: SymbolPrimitive[];
  tile_viewbox?: [number, number];
}

export interface SymbolAssetDocument {
  schema_version: number;
  kind: AssetKind;
  name?: string;
  viewbox?: [number, number];
  primitives?: SymbolPrimitive[];
  pattern?: TexturePattern;
  default_params?: Record<string, unknown>;
  source?: Record<string, string>;
  content_digest: string;
}

// ─── MapSpec 绑定块（与 mapspec_schema.py MapSpecLayerSymbolAsset 对齐） ──

export interface CategoryRule {
  key: string | number | boolean;
  asset_id: string;
  revision: number;
  content_digest: string;
  params?: Record<string, unknown>;
}

export interface CategoryBinding {
  field: string;
  key_type: 'number' | 'string' | 'bool';
  map: CategoryRule[];
  other_mode: 'fallback_asset' | 'keep_current';
  other_asset?: string;
  other_revision?: number;
  other_content_digest?: string;
  other_params?: Record<string, unknown>;
}

export interface SymbolAssetBinding {
  schema_version: 1;
  asset_id: string;
  revision: number;
  content_digest: string;
  kind: AssetKind;
  params?: Record<string, unknown>;
  category_binding?: CategoryBinding;
}

// ─── 归一化（Python normalize_* 的 TS 镜像；同错误码语义） ────────────────

export class SymbolAssetParamError extends Error {
  constructor(public code: string, message: string) {
    super(`${code}: ${message}`);
  }
}

const HEX_RE = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/;

function num(
  v: unknown, lo: number, hi: number, where: string, def?: number,
): number {
  if (v === undefined || v === null) {
    if (def === undefined) throw new SymbolAssetParamError('MISSING_PARAM', where);
    return def;
  }
  if (typeof v !== 'number' || !Number.isFinite(v) || v < lo || v > hi) {
    throw new SymbolAssetParamError(
      'PARAM_OUT_OF_RANGE', `${where}: ${String(v)} ∉ [${lo},${hi}]`);
  }
  return v;
}

function hexColor(v: unknown, where: string): string {
  const s = String(v ?? '').trim();
  if (!HEX_RE.test(s)) {
    throw new SymbolAssetParamError('INVALID_COLOR', `${where}: ${String(v)}`);
  }
  return s.toLowerCase();
}

export function normalizePointParams(
  raw: Record<string, unknown> | undefined | null,
  doc?: SymbolAssetDocument | null,
): PointParams {
  const r = { ...(raw ?? {}) };
  const unknown = Object.keys(r).filter(
    (k) => !['size_px', 'anchor', 'rotation_deg', 'color', 'opacity',
             'rotation_alignment'].includes(k));
  if (unknown.length) {
    throw new SymbolAssetParamError('UNKNOWN_PARAM', unknown.join(','));
  }
  const d = (doc?.default_params ?? {}) as Record<string, unknown>;
  if (r.rotation_alignment !== undefined && r.rotation_alignment !== 'viewport') {
    throw new SymbolAssetParamError(
      'UNSUPPORTED_ALIGNMENT', String(r.rotation_alignment));
  }
  const out: PointParams = {
    size_px: num(r.size_px ?? d.size_px, POINT_SIZE_PX_MIN, POINT_SIZE_PX_MAX,
      'size_px', 16.0),
    anchor: (r.anchor ?? d.anchor ?? 'center') as PointAnchor,
    rotation_deg: num(r.rotation_deg ?? d.rotation_deg, ROTATION_MIN,
      ROTATION_MAX, 'rotation_deg', 0.0),
    opacity: num(r.opacity ?? d.opacity, 0, 1, 'opacity', 1.0),
  };
  if (!ANCHORS.includes(out.anchor)) {
    throw new SymbolAssetParamError('UNKNOWN_ANCHOR', out.anchor);
  }
  const color = r.color ?? d.color;
  if (color !== undefined && color !== null) out.color = hexColor(color, 'color');
  return out;
}

export function normalizeTextureParams(
  raw: Record<string, unknown> | undefined | null,
  doc?: SymbolAssetDocument | null,
): TextureParams {
  const r = { ...(raw ?? {}) };
  const allowed = ['spacing_px', 'angle_deg', 'stroke_width_px', 'fg_color',
                   'bg_color', 'pattern_scale'];
  const unknown = Object.keys(r).filter((k) => !allowed.includes(k));
  if (unknown.length) {
    throw new SymbolAssetParamError('UNKNOWN_PARAM', unknown.join(','));
  }
  const d = (doc?.default_params ?? {}) as Record<string, unknown>;
  const out: TextureParams = {
    spacing_px: num(r.spacing_px ?? d.spacing_px, TEXTURE_SPACING_MIN,
      TEXTURE_SPACING_MAX, 'spacing_px', 12.0),
    angle_deg: num(r.angle_deg ?? d.angle_deg, -90, 90, 'angle_deg', 45.0),
    stroke_width_px: num(r.stroke_width_px ?? d.stroke_width_px,
      TEXTURE_STROKE_MIN, TEXTURE_STROKE_MAX, 'stroke_width_px', 1.5),
    pattern_scale: num(r.pattern_scale ?? d.pattern_scale, TEXTURE_SCALE_MIN,
      TEXTURE_SCALE_MAX, 'pattern_scale', 1.0),
    fg_color: hexColor(r.fg_color ?? d.fg_color ?? '#334155', 'fg_color'),
    bg_color: '',
  };
  const bg = r.bg_color ?? d.bg_color ?? 'transparent';
  out.bg_color = bg === 'transparent' || bg === 'none'
    ? 'transparent' : hexColor(bg, 'bg_color');
  return out;
}

// ─── 注册 image id（与 Python defs_id 同式；跨实例去重键） ────────────────

/** 简版参数指纹（与后端 params_digest 前 16 hex 对齐用途：稳定去重键）。 */
export function stableKey(payload: unknown): string {
  const s = JSON.stringify(payload);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < s.length; i++) {
    h1 ^= s.charCodeAt(i);
    h1 = Math.imul(h1, 16777619) >>> 0;
    h2 = (h2 + Math.imul(s.charCodeAt(i) + i, 2654435761)) >>> 0;
  }
  return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0'));
}

export function pointImageId(
  digest: string, params: Record<string, unknown>,
): string {
  return `cartoasset-${digest.slice(7, 19)}-${stableKey(params)}`;
}

export function patternImageId(
  digest: string, params: Record<string, unknown>,
): string {
  return `cartoap-${digest.slice(7, 19)}-${stableKey(params)}`;
}

/** 锚点在 viewBox 域的偏移（与 Python anchor_offset 同式）。 */
export function anchorOffset(
  doc: SymbolAssetDocument, anchor: PointAnchor,
): [number, number] {
  const [w, h] = doc.viewbox ?? [24, 24];
  const hx: Record<string, number> = { left: -w / 2, center: 0, right: w / 2 };
  const vy: Record<string, number> = { top: -h / 2, center: 0, bottom: h / 2 };
  // 与 Python anchor_offset 同式：未命中轴（如 bottom 的 x 轴）→ 0
  const ax = (anchor.includes('-') ? hx[anchor.split('-')[1]] : hx[anchor]) ?? 0;
  const ay = (anchor.includes('-') ? vy[anchor.split('-')[0]] : vy[anchor]) ?? 0;
  return [ax, ay];
}
