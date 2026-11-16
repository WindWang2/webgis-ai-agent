/**
 * Pure pixel <-> viewport/container coordinate transforms for the G48 dual-view
 * GCP source image viewer.
 *
 * All pixel coordinates match backend `app/services/georeferencing/solver.py`:
 * top-left of pixel (0, 0) is (0.0, 0.0) and pixel center is (col + 0.5, row + 0.5),
 * bounded strictly within [0, imageWidth] x [0, imageHeight].
 */

export interface ImageDimensions {
  width: number;
  height: number;
}

export interface ViewportSize {
  width: number;
  height: number;
}

export interface ViewTransform {
  /** Base fit scale from image pixels to container CSS pixels at zoom === 1. */
  fitScale: number;
  /** User interactive zoom factor (>= MIN_ZOOM). */
  zoom: number;
  /** Pan offset in container CSS pixels relative to container top-left. */
  panX: number;
  panY: number;
}

export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 32;

export function clamp(val: number, min: number, max: number): number {
  if (!Number.isFinite(val)) return min;
  return Math.min(max, Math.max(min, val));
}

export function clampZoom(zoom: number): number {
  return clamp(zoom, MIN_ZOOM, MAX_ZOOM);
}

/**
 * Compute initial centered fit-to-container transform (`zoom = 1`) with optional padding.
 */
export function computeFitTransform(
  image: ImageDimensions,
  viewport: ViewportSize,
  paddingPx = 12,
): ViewTransform {
  if (image.width <= 0 || image.height <= 0 || viewport.width <= 0 || viewport.height <= 0) {
    return { fitScale: 1, zoom: 1, panX: 0, panY: 0 };
  }
  const availW = Math.max(16, viewport.width - paddingPx * 2);
  const availH = Math.max(16, viewport.height - paddingPx * 2);
  const fitScale = Math.min(availW / image.width, availH / image.height);
  const renderedW = image.width * fitScale;
  const renderedH = image.height * fitScale;
  const panX = (viewport.width - renderedW) / 2;
  const panY = (viewport.height - renderedH) / 2;
  return { fitScale, zoom: 1, panX, panY };
}

/** Effective CSS pixels per source image pixel. */
export function effectiveScale(transform: ViewTransform): number {
  return Math.max(1e-6, transform.fitScale * clampZoom(transform.zoom));
}

/**
 * Map a pointer event position (`clientX - rectLeft`, `clientY - rectTop` in CSS px)
 * to source image pixel coordinates `(px, py)`.
 */
export function containerToImagePixel(
  containerX: number,
  containerY: number,
  transform: ViewTransform,
  image: ImageDimensions,
): { px: number; py: number; inside: boolean } {
  const scale = effectiveScale(transform);
  const rawPx = (containerX - transform.panX) / scale;
  const rawPy = (containerY - transform.panY) / scale;
  const inside = rawPx >= 0 && rawPx <= image.width && rawPy >= 0 && rawPy <= image.height;
  return {
    px: Number(clamp(rawPx, 0, Math.max(0, image.width)).toFixed(3)),
    py: Number(clamp(rawPy, 0, Math.max(0, image.height)).toFixed(3)),
    inside,
  };
}

/**
 * Map source image pixel `(px, py)` back into container CSS pixels for SVG markers.
 */
export function imagePixelToContainer(
  px: number,
  py: number,
  transform: ViewTransform,
): { x: number; y: number } {
  const scale = effectiveScale(transform);
  return {
    x: transform.panX + px * scale,
    y: transform.panY + py * scale,
  };
}

/**
 * Zoom around a specific container pivot `(anchorX, anchorY)` so the image point under
 * the cursor stays fixed during wheel or button zoom.
 */
export function zoomAroundPoint(
  transform: ViewTransform,
  nextZoom: number,
  anchorX: number,
  anchorY: number,
): ViewTransform {
  const clampedZoom = clampZoom(nextZoom);
  const oldScale = effectiveScale(transform);
  const newScale = Math.max(1e-6, transform.fitScale * clampedZoom);
  const ratio = newScale / oldScale;
  return {
    fitScale: transform.fitScale,
    zoom: clampedZoom,
    panX: anchorX - (anchorX - transform.panX) * ratio,
    panY: anchorY - (anchorY - transform.panY) * ratio,
  };
}

/**
 * Compute exaggerated SVG residual arrow endpoint `(x2, y2)` clamped cleanly
 * so outliers remain readable without blowing off the canvas.
 */
export function residualArrowEnd(
  originX: number,
  originY: number,
  dxPx: number,
  dyPx: number,
  scale: number,
  exaggeration = 8,
  maxArrowLenCssPx = 72,
): { x2: number; y2: number } {
  const vx = dxPx * scale * exaggeration;
  const vy = dyPx * scale * exaggeration;
  const norm = Math.hypot(vx, vy);
  if (!Number.isFinite(norm) || norm <= 1e-6) {
    return { x2: originX, y2: originY };
  }
  const factor = norm > maxArrowLenCssPx ? maxArrowLenCssPx / norm : 1;
  return {
    x2: originX + vx * factor,
    y2: originY + vy * factor,
  };
}
