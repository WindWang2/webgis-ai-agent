/**
 * Raster Tile Zoom Capture Plan —— 高 DPI 栅格细节折中（V11 W6.3，ADR-0166）。
 *
 * 评估结论（docs/dev/ac-v11-highdpi-evaluation.md 量化记录）：
 * - ``setPixelRatio(dpi/96)`` 是**真重渲染**（矢量/符号层受益；tilezoom-dpr
 *   probe 实证栅格瓦片取图 zoom 不随 DPR 变化）→ 重建实例方案对栅格细节
 *   **同样无增益**（取图 zoom 由 view zoom 决定，与实例生命周期无关）。
 * - 唯一有增益的路径是「**取图期放大 view zoom、导出期重采样回目标尺寸**」：
 *   captureZoom = baseZoom + log2(dpi/96)（栅格瓦片多取 log2(dpi/96) 级），
 *   resampleFactor = dpi/96。代价：取图期视野缩小（有效像素面积不变——
 *   细节来自更深的瓦片层级而非插值）。
 *
 * 本模块只产出**规划**（纯函数，确定性）；live 接线按 opt-in 语义由导出
 * 命令决定（默认路径保持 V10 行为 —— setPixelRatio + 诚实披露）。
 */

export interface TileZoomCapturePlan {
  /** 取图期目标 zoom（通常高于目标缩放；≤ maxZoom 时有效）。 */
  captureZoom: number;
  /** 导出期重采样系数（capture 画面 → 目标尺寸的比例直觉）。 */
  resampleFactor: number;
  /** 实际可用的 zoom 增益级数（受 maxZoom 封顶；0 = 无增益，保持 V10）。 */
  zoomGainLevels: number;
  /** 量化披露：增益级数与残余栅格细节受限说明。 */
  disclosure: string;
}

/**
 * 高 DPI 栅格取图规划（确定性纯函数）。
 *
 * @param baseZoom  当前 view zoom（导出目标视野）
 * @param dpi       目标 DPI（96 为原生比率）
 * @param maxZoom   底图源 maxzoom（瓦片源物理上限）
 */
export function planTileZoomCapture(
  baseZoom: number,
  dpi: number,
  maxZoom: number,
): TileZoomCapturePlan {
  const ratio = Math.max(1, dpi / 96);
  const desiredGain = Math.log2(ratio);
  const headroom = Math.max(0, maxZoom - baseZoom);
  const zoomGainLevels = Math.min(desiredGain, headroom);
  const captureZoom = Math.min(baseZoom + zoomGainLevels, maxZoom);
  const resampleFactor = ratio;
  const effective = zoomGainLevels >= 0.5
    ? `栅格取图 zoom 提升 ${zoomGainLevels.toFixed(1)} 级（${baseZoom.toFixed(1)}→${captureZoom.toFixed(1)}）后重采样 ${ratio.toFixed(2)}×`
    : `底图 maxzoom=${maxZoom.toFixed(1)} 无 headroom —— 栅格细节保持原生（与 V10 等同，如实披露）`;
  return {
    captureZoom,
    resampleFactor,
    zoomGainLevels,
    disclosure: effective,
  };
}
