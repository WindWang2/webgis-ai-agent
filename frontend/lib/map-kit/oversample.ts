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

export type MapExtent = [number, number, number, number]; // [minLon, minLat, maxLon, maxLat]

export interface ViewportDimensions {
  width: number;
  height: number;
}

export interface TileGridItem extends ViewportDimensions {
  url: string;
  x: number;
  y: number;
  z: number;
  tileX: number;
  tileY: number;
}

export interface TileGridOptions extends ViewportDimensions {
  bounds: MapExtent; // [minLon, minLat, maxLon, maxLat]
  padding: number;
  targetDpi: number;
  tileUrlTemplate: string;
  baseZoom?: number;
}

export function mercY(lat: number): number {
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
  return Math.log(Math.tan(Math.PI / 4 + (clampedLat * Math.PI) / 360));
}

export function lonToTileX(lon: number, zoom: number): number {
  const n = Math.pow(2, zoom);
  return Math.max(0, Math.min(n - 1, Math.floor(((lon + 180) / 360) * n)));
}

export function latToTileY(lat: number, zoom: number): number {
  const n = Math.pow(2, zoom);
  const latRad = (Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI) / 180;
  return Math.max(0, Math.min(n - 1, Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n)));
}

export function tileXToLon(x: number, zoom: number): number {
  const n = Math.pow(2, zoom);
  return (x / n) * 360 - 180;
}

export function tileYToLat(y: number, zoom: number): number {
  const n = Math.pow(2, zoom);
  const yVal = Math.PI * (1 - (2 * y) / n);
  return (Math.atan(Math.sinh(yVal)) * 180) / Math.PI;
}

export function substituteTileUrl(template: string, x: number, y: number, z: number): string {
  let url = template
    .replace('{z}', String(z))
    .replace('{x}', String(x))
    .replace('{y}', String(y))
    .replace('{TILEMATRIX}', String(z))
    .replace('{TILECOL}', String(x))
    .replace('{TILEROW}', String(y));
  if (url.includes('{s}')) {
    const subdomains = ['a', 'b', 'c', '0', '1', '2', '3'];
    const s = subdomains[(x + y) % subdomains.length];
    url = url.replace('{s}', s);
  }
  return url;
}

/**
 * Calculates the oversampled tile grid for raster basemap rendering (#260).
 */
export function resolveOversampledTileGrid(options: TileGridOptions): TileGridItem[] {
  const { bounds, width, height, padding, targetDpi, tileUrlTemplate, baseZoom: customBaseZoom } = options;
  const [minLon, minLat, maxLon, maxLat] = bounds;

  const isTemplate = /\{[xyzs]|TILECOL|TILEROW|TILEMATRIX\}/.test(tileUrlTemplate);
  const zoomBoost = computeOversampleBoost(targetDpi);

  if (!isTemplate) {
    return [{
      url: tileUrlTemplate,
      x: 0,
      y: 0,
      width,
      height,
      z: customBaseZoom ?? 0,
      tileX: 0,
      tileY: 0,
    }];
  }

  const rangeX = (maxLon - minLon) || 1.0;
  const rangeY = (maxLat - minLat) || 1.0;

  const mercMinY = mercY(minLat);
  const mercMaxY = mercY(maxLat);
  const rangeMercY = (mercMaxY - mercMinY) || 1.0;

  const calculatedBase = Math.max(0, Math.min(19, Math.floor(Math.log2(360 / Math.max(Math.max(rangeX, rangeY), 0.0001)))));
  const baseZoom = customBaseZoom ?? calculatedBase;
  const z = Math.max(0, Math.min(19, baseZoom + zoomBoost));

  const xMin = lonToTileX(minLon, z);
  const xMax = lonToTileX(maxLon, z);
  const yMin = latToTileY(maxLat, z);
  const yMax = latToTileY(minLat, z);

  const items: TileGridItem[] = [];

  const projX = (lon: number) => padding + ((lon - minLon) / rangeX) * (width - padding * 2);
  const projY = (lat: number) => {
    const normY = (mercY(lat) - mercMinY) / rangeMercY;
    return height - padding - normY * (height - padding * 2);
  };

  for (let tx = xMin; tx <= xMax; tx++) {
    for (let ty = yMin; ty <= yMax; ty++) {
      const lonW = tileXToLon(tx, z);
      const lonE = tileXToLon(tx + 1, z);
      const latN = tileYToLat(ty, z);
      const latS = tileYToLat(ty + 1, z);

      const px = projX(lonW);
      const py = projY(latN);
      const pw = projX(lonE) - projX(lonW);
      const ph = projY(latS) - projY(latN);

      items.push({
        url: substituteTileUrl(tileUrlTemplate, tx, ty, z),
        x: px,
        y: py,
        width: pw,
        height: ph,
        z,
        tileX: tx,
        tileY: ty,
      });
    }
  }

  return items;
}
