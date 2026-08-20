/**
 * Pure probe helpers for the Runtime Validator probe DSL.
 *
 * These functions are pure (no browser / I/O) so they can be unit-tested via
 * Vitest without Playwright. The browser-side executor in runtime-validate.ts
 * reuses them (hexToRgb / colorWithinTolerance) and re-implements the DOM-side
 * logic (layer-exists, feature-count, pixel-color) inside page.evaluate.
 */

export function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const cleaned = hex.replace(/^#/, "");
  if (cleaned.length === 3) {
    return {
      r: parseInt(cleaned[0] + cleaned[0], 16),
      g: parseInt(cleaned[1] + cleaned[1], 16),
      b: parseInt(cleaned[2] + cleaned[2], 16),
    };
  }
  if (cleaned.length === 6) {
    return {
      r: parseInt(cleaned.slice(0, 2), 16),
      g: parseInt(cleaned.slice(2, 4), 16),
      b: parseInt(cleaned.slice(4, 6), 16),
    };
  }
  throw new Error(`hexToRgb: invalid hex color "${hex}"`);
}

export function colorWithinTolerance(
  actual: { r: number; g: number; b: number },
  expectedHex: string,
  tol = 16
): boolean {
  const expected = hexToRgb(expectedHex);
  return (
    Math.abs(actual.r - expected.r) <= tol &&
    Math.abs(actual.g - expected.g) <= tol &&
    Math.abs(actual.b - expected.b) <= tol
  );
}

/**
 * Find dominant color in a 5×5 (or (2*half+1)²) window centered at (cx, cy).
 * Returns null if window is empty/out-of-bounds (no pixels sampled).
 * Ties → color closest to center wins (smaller Chebyshev distance).
 */
export function dominantColorInWindow(
  png: { width: number; height: number; data: Uint8Array | Buffer },
  cx: number,
  cy: number,
  half = 2
): { r: number; g: number; b: number } | null {
  const { width, height, data } = png;
  // Map color key → { count, firstDist, rgb }
  const counts = new Map<string, { count: number; dist: number; rgb: { r: number; g: number; b: number } }>();

  for (let dy = -half; dy <= half; dy++) {
    for (let dx = -half; dx <= half; dx++) {
      const x = cx + dx;
      const y = cy + dy;
      if (x < 0 || x >= width || y < 0 || y >= height) continue;
      const off = (y * width + x) * 4;
      const a = data[off + 3];
      if (a === 0) continue; // skip transparent
      const r = data[off];
      const g = data[off + 1];
      const b = data[off + 2];
      const key = `${r},${g},${b}`;
      const dist = Math.max(Math.abs(dx), Math.abs(dy));
      const existing = counts.get(key);
      if (existing) {
        existing.count++;
      } else {
        counts.set(key, { count: 1, dist, rgb: { r, g, b } });
      }
    }
  }

  if (counts.size === 0) return null;

  let best: { count: number; dist: number; rgb: { r: number; g: number; b: number } } | null = null;
  for (const v of counts.values()) {
    if (
      best === null ||
      v.count > best.count ||
      (v.count === best.count && v.dist < best.dist)
    ) {
      best = v;
    }
  }
  return best!.rgb;
}
