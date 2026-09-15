/**
 * GeoAI 面板坐标数学（纯函数；与后端 GeoPrompt artifact 契约同源）。
 *
 * 预览图与源栅格同域（bounds/宽高来自 GET /geoai/preview），屏幕像素 ↔
 * 地图坐标的仿射由 bounds 线性插值唯一确定——不引入第二份栅格真值。
 */

export interface PreviewMeta {
  preview_width: number;
  preview_height: number;
  source_width: number;
  source_height: number;
  crs: string | null;
  bounds: [number, number, number, number]; // [minx, miny, maxx, maxy]
}

export interface PointPrompt {
  kind: 'point';
  x: number;
  y: number;
}

export interface BoxPrompt {
  kind: 'box';
  x: number;
  y: number;
  w: number;
  h: number;
}

export type MapPrompt = PointPrompt | BoxPrompt;

export function screenToMap(
  px: number,
  py: number,
  meta: PreviewMeta,
): { x: number; y: number } {
  const [minx, miny, maxx, maxy] = meta.bounds;
  const fx = px / Math.max(1, meta.preview_width);
  const fy = py / Math.max(1, meta.preview_height);
  return {
    x: minx + fx * (maxx - minx),
    y: maxy - fy * (maxy - miny),
  };
}

export function mapToScreen(
  x: number,
  y: number,
  meta: PreviewMeta,
): { px: number; py: number } {
  const [minx, miny, maxx, maxy] = meta.bounds;
  const fx = (x - minx) / (maxx - minx);
  const fy = (maxy - y) / (maxy - miny);
  return {
    px: Math.round(fx * meta.preview_width),
    py: Math.round(fy * meta.preview_height),
  };
}

/** 拖拽（屏幕坐标）→ 地图坐标 box prompt（正的 w/h，无翻转歧义）。 */
export function boxFromDrag(
  startPx: number,
  startPy: number,
  endPx: number,
  endPy: number,
  meta: PreviewMeta,
): BoxPrompt {
  const a = screenToMap(
    Math.min(startPx, endPx),
    Math.min(startPy, endPy),
    meta,
  );
  const b = screenToMap(
    Math.max(startPx, endPx),
    Math.max(startPy, endPy),
    meta,
  );
  // 屏幕 y 向下、地图 y 向上：a 是地图北缘、b 是南缘——box 取南缘为原点，
  // h 为正（与 foundation.prompts_to_pixel 的 (x,y,w,h) 北向上口径一致）。
  return {
    kind: 'box',
    x: a.x,
    y: Math.min(a.y, b.y),
    w: b.x - a.x,
    h: Math.abs(a.y - b.y),
  };
}

/** prompts → GeoPrompt artifact payload（CRS 来自预览元数据）。 */
export function buildArtifactPayload(
  prompts: MapPrompt[],
  meta: PreviewMeta,
): {
  schema_version: number;
  crs: string | null;
  points: number[][];
  boxes: number[][];
  provenance: { created_by: string; note: string };
} {
  return {
    schema_version: 1,
    crs: meta.crs,
    points: prompts
      .filter((p): p is PointPrompt => p.kind === 'point')
      .map((p) => [p.x, p.y]),
    boxes: prompts
      .filter((p): p is BoxPrompt => p.kind === 'box')
      .map((p) => [p.x, p.y, p.w, p.h]),
    provenance: { created_by: 'geoai-panel', note: 'interactive prompt' },
  };
}
