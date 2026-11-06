/** Shared LayerStyle to canonical paint projection. */
export function layerStyleToPaint(style: Record<string, unknown>): Record<string, unknown> {
  const paint: Record<string, unknown> = {};
  if (typeof style.color === 'string') paint.color = style.color;
  if (typeof style.strokeColor === 'string') paint.strokeColor = style.strokeColor;
  if (typeof style.strokeWidth === 'number') { paint.strokeWidth = style.strokeWidth; paint.width = style.strokeWidth; }
  if (typeof style.opacity === 'number') paint.opacity = style.opacity;
  if (typeof style.pointSize === 'number') paint.radius = style.pointSize;
  return paint;
}
