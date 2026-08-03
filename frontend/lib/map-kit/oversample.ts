/**
 * Oversample boost helper shared by the MapSpec-to-SVG compiler twins and the
 * HD export path.
 *
 * Per spec #268 ("Raster Basemap Oversampling"), high-DPI (300+ DPI) exports
 * fetch raster basemap tiles at an oversampled zoom level to eliminate
 * pixelation during raster-vector compositing. The boost factor is
 * `log2(dpi / 96)` clamped to `[0, 2]`: 0 at 96 DPI, +1 at 192 DPI, +2 at
 * 300+ DPI.
 *
 * This module holds the ONE definition of the formula so the TS compiler twin
 * and the exporter cannot drift (review Standards finding: the formula was
 * previously inlined in three places). NOTE: this computes the boost factor
 * only; the actual oversampled tile fetch is the separate #260
 * tile-rasterization-policy ticket.
 */

export function computeOversampleBoost(dpi: number): number {
  return Math.min(2, Math.max(0, Math.round(Math.log2(dpi / 96))));
}

/**
 * Returns the oversampled zoom level for a given base zoom and DPI.
 * Convenience wrapper: `baseZoom + computeOversampleBoost(dpi)`.
 */
export function getOversampledZoom(baseZoom: number, dpi: number = 96): number {
  return baseZoom + computeOversampleBoost(dpi);
}
