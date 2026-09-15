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

/**
 * 屏幕 → 坐标。CRS 面：像元中心约定（(px+0.5)/w——review fix：边缘
 * 约定对每个提示有半降采样块的常量偏移）。像素面（crs=null，无地理参考
 * 栅格）：直接源像元坐标（y 不翻转——后端契约 = 左上原点）。
 */
export function screenToMap(
  px: number,
  py: number,
  meta: PreviewMeta,
): { x: number; y: number } {
  const fx = (px + 0.5) / Math.max(1, meta.preview_width);
  const fy = (py + 0.5) / Math.max(1, meta.preview_height);
  if (!meta.crs) {
    return {
      x: fx * meta.source_width,
      y: fy * meta.source_height,
    };
  }
  const [minx, miny, maxx, maxy] = meta.bounds;
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
  if (!meta.crs) {
    return {
      px: Math.round(
        (x / Math.max(1, meta.source_width)) * meta.preview_width - 0.5,
      ),
      py: Math.round(
        (y / Math.max(1, meta.source_height)) * meta.preview_height - 0.5,
      ),
    };
  }
  const [minx, miny, maxx, maxy] = meta.bounds;
  const fx = (x - minx) / (maxx - minx);
  const fy = (maxy - y) / (maxy - miny);
  return {
    px: Math.round(fx * meta.preview_width - 0.5),
    py: Math.round(fy * meta.preview_height - 0.5),
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
  // 屏幕 y 向下：像素面（crs=null）y 同向（a 小 b 大）；地图面 y 向上
  // （a 北 b 南）——两种情况都取 min 为原点、绝对值为跨度的正 w/h。
  return {
    kind: 'box',
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    w: Math.abs(b.x - a.x),
    h: Math.abs(b.y - a.y),
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
