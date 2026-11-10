/**
 * G20 —— 数值栅格瓦片 URL 的唯一构造面（compiler / runtime 共用）。
 *
 * 纪律：持久 MapSpec 只存数据 ref + style_id；session_id 由 runtime 在
 * 运行时注入占位符，owner token 由既有 `buildTileTransformRequest` 放进
 * header —— URL、spec、日志、导出产物都不含凭证。
 *
 * 本模块**只用相对导入**，因为 headless compiler CLI 不解析 `@/` 别名。
 */

/** compiler 输出的会话占位符；runtime 以当前 session id 替换。 */
export const RASTER_SESSION_PLACEHOLDER = "__SESSION_ID__";

/** 同源双层各自派生 MapLibre source id —— 共享数据 ref 不串色、不串缓存。 */
export function numericRasterSourceId(sourceId: string, layerId: string): string {
  return `${sourceId}::${layerId}`;
}

/** 已提交 style_id 进 URL；后端与当前 MapSpec 比对，旧 URL 直接 409。 */
export function buildNumericRasterTileUrl(
  refId: string,
  layerId: string,
  styleId: string,
): string {
  const q = new URLSearchParams({
    session_id: RASTER_SESSION_PLACEHOLDER,
    layer_id: layerId,
    style_id: styleId,
  }).toString();
  return `/api/v1/raster-authoring/tiles/${encodeURIComponent(refId)}/{z}/{x}/{y}.png?${q}`;
}

/** 返回 null = 会话未绑定，runtime 必须拒绝挂载而不是画出假地图。 */
export function resolveNumericRasterTileUrl(
  template: string,
  sessionId: string | null | undefined,
): string | null {
  if (!sessionId || !template.includes(RASTER_SESSION_PLACEHOLDER)) return null;
  return template.split(RASTER_SESSION_PLACEHOLDER).join(encodeURIComponent(sessionId));
}

/** 从编译产物的 tiles URL 读出 style_id（图例/导出披露用）。 */
export function styleIdFromTileUrl(template: string): string | null {
  const marker = "style_id=";
  const at = template.indexOf(marker);
  if (at < 0) return null;
  const tail = template.slice(at + marker.length);
  const end = tail.indexOf("&");
  try {
    return decodeURIComponent(end < 0 ? tail : tail.slice(0, end));
  } catch {
    return null;
  }
}
