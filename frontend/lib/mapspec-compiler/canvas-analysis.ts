/**
 * Pure canvas-image analysis for the Runtime Validator (Seam C support).
 *
 * All functions here are pure: given a decoded RGBA pixel buffer, they return
 * statistics with no browser or I/O dependency. This keeps the canvas checks
 * unit-testable (Seam A) separately from the Playwright driving script.
 *
 * Thresholds match the spec's "non-blank" contract:
 *  - transparentRatio: fraction of fully-transparent pixels (alpha == 0).
 *    A map canvas that is fully transparent has nothing rendered on it.
 *  - dominantRatio: fraction of pixels equal to the single most common colour.
 *    A near-monochrome canvas (e.g. one solid fill) suggests no real data drawn.
 *  - luminanceStdDev: spread of per-pixel luminance. A flat/blank image has ~0.
 */

export interface PixelStats {
  width: number;
  height: number;
  pixelCount: number; // non-fully-transparent pixels considered
  transparentRatio: number; // 0..1
  dominantRatio: number; // 0..1, share of the most common opaque colour
  luminanceStdDev: number; // 0..255, std-dev of luminance over opaque pixels
  dominantColor: { r: number; g: number; b: number } | null;
}

/** ITU-R BT.601 luminance, matching MapLibre's own perceptual weighting. */
function luminance(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/**
 * Analyse an RGBA byte buffer (length === width*height*4).
 * Returns the statistics consumed by the Runtime Validator's blank check.
 */
export function analyseCanvas(rgba: Uint8Array, width: number, height: number): PixelStats {
  const totalPixels = width * height;
  if (rgba.length < totalPixels * 4) {
    throw new Error(
      `analyseCanvas: buffer of ${rgba.length} bytes too small for ${width}x${height} RGBA`
    );
  }

  let transparentCount = 0;
  const colourCounts = new Map<string, number>();
  const luminances: number[] = [];

  for (let i = 0; i < totalPixels; i++) {
    const off = i * 4;
    const alpha = rgba[off + 3];
    if (alpha === 0) {
      transparentCount++;
      continue;
    }
    const r = rgba[off];
    const g = rgba[off + 1];
    const b = rgba[off + 2];
    // Quantise to 16 levels per channel so near-identical colours collapse to
    // one bucket — otherwise JPEG-like anti-aliasing noise fragments the count.
    const key = `${r >> 4}|${g >> 4}|${b >> 4}`;
    colourCounts.set(key, (colourCounts.get(key) ?? 0) + 1);
    luminances.push(luminance(r, g, b));
  }

  const opaqueCount = totalPixels - transparentCount;
  // Find the most common opaque colour. Iterate via Array.from to avoid the
  // Map downlevelIteration constraint while keeping a for...of loop shape.
  let dominantKey: string | null = null;
  let dominantCount = 0;
  const colourEntries: Array<[string, number]> = Array.from(colourCounts.entries());
  for (const [key, count] of colourEntries) {
    if (count > dominantCount) {
      dominantCount = count;
      dominantKey = key;
    }
  }

  let dominantColor: PixelStats["dominantColor"] = null;
  if (dominantKey) {
    const [r, g, b] = dominantKey.split("|").map((n) => parseInt(n, 10) << 4);
    dominantColor = { r, g, b };
  }

  const mean =
    luminances.length > 0
      ? luminances.reduce((a, b) => a + b, 0) / luminances.length
      : 0;
  const variance =
    luminances.length > 0
      ? luminances.reduce((a, l) => a + (l - mean) ** 2, 0) / luminances.length
      : 0;
  const luminanceStdDev = Math.sqrt(variance);

  return {
    width,
    height,
    pixelCount: opaqueCount,
    transparentRatio: totalPixels > 0 ? transparentCount / totalPixels : 1,
    dominantRatio: opaqueCount > 0 ? dominantCount / opaqueCount : 1,
    luminanceStdDev,
    dominantColor,
  };
}

export interface BlankVerdict {
  blank: boolean;
  reason: string;
}

/**
 * Decide whether a canvas is "blank" per the spec: a blank canvas fails
 * acceptance because it likely means nothing rendered. This is a *risk signal*
 * — a legitimate single-colour map can false-positive, so a "blank" verdict
 * must not be silently treated as a hard pass either.
 */
export function isBlank(stats: PixelStats): BlankVerdict {
  if (stats.transparentRatio >= 0.99) {
    return { blank: true, reason: "canvas is fully (or near-fully) transparent" };
  }
  if (stats.dominantRatio >= 0.98) {
    return { blank: true, reason: "canvas is near-monochrome (one colour dominates)" };
  }
  if (stats.luminanceStdDev < 3) {
    return { blank: true, reason: "canvas luminance variance is negligible (flat image)" };
  }
  return { blank: false, reason: "canvas shows visible variation" };
}
