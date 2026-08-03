/**
 * MapSpec-to-SVG Compiler Target
 *
 * Compiles a declarative MapSpec (sources + layers + paint rules) directly into
 * resolution-independent SVG vector markup, scaling stroke-width and symbol radii
 * by (targetDpi / 72) for 300+ DPI high-definition printing.
 */

export interface MapSpecToSvgOptions {
  targetDpi?: number;
  width?: number;
  height?: number;
  padding?: number;
  includeMarginalia?: boolean;
}

import { computeOversampleBoost } from "../map-kit/oversample";

/**
 * Escapes a string for safe interpolation into an SVG attribute value.
 * MapSpec paint values (colors, opacities) flow directly into attributes like
 * fill="...", so a crafted value containing `"` could break out of the
 * attribute and inject markup (attribute-injection / XSS). This mirrors the
 * minimal set HTML-escaped by the Python twin (html.escape(s, quote=True)).
 */
function escapeSvgAttr(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Formats a number for byte-identical parity with the Python twin.
 *
 * Both twins reduce every numeric SVG attribute to a canonical "minimal" form:
 * round to 2 decimals, then strip trailing zeros and a dangling decimal point.
 * So `50.0` -> `50`, `1.0` -> `1`, `0.6` -> `0.6`, `20.83` -> `20.83`.
 *
 * Rounding note: Python's `f"{v:.2f}"` uses round-half-to-even (banker's),
 * JS's `Number.toFixed(2)` uses round-half-away-from-zero. These only diverge
 * at exact .xx5 midpoints; the compiler's DPI-scaled values (multiples of
 * dpi_scale = targetDpi/72 applied to integer base sizes) never land on such
 * midpoints in practice. The parity tests pin the exact expected strings, so
 * any future divergence fails the suite.
 *
 * Contract: both twins emit `50` (not `50.0`), `1` (not `1.0`). The parity
 * tests assert ONE canonical form; drift fails the test.
 */
function fmtNum(v: number): string {
  let s = v.toFixed(2);
  if (s.includes(".")) {
    s = s.replace(/0+$/, "").replace(/\.$/, "");
  }
  return s;
}

export function compileMapSpecToSvg(
  mapspec: any,
  options: MapSpecToSvgOptions = {}
): string {
  const targetDpi = options.targetDpi ?? 300;
  const width = options.width ?? 1200;
  const height = options.height ?? 800;
  const padding = options.padding ?? 40;
  const dpiScale = targetDpi / 72;

  // 1. Gather all coordinates across sources to compute extent bounding box
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;

  const sources = mapspec?.sources || {};
  Object.values(sources).forEach((src: any) => {
    const geojson = src?.data;
    if (!geojson) return;
    const features = geojson.type === "FeatureCollection" ? geojson.features : [geojson];

    features.forEach((feat: any) => {
      const geom = feat?.geometry;
      if (!geom) return;

      const extractCoords = (c: any) => {
        if (typeof c[0] === "number" && typeof c[1] === "number") {
          minX = Math.min(minX, c[0]);
          maxX = Math.max(maxX, c[0]);
          minY = Math.min(minY, c[1]);
          maxY = Math.max(maxY, c[1]);
        } else if (Array.isArray(c)) {
          c.forEach(extractCoords);
        }
      };
      extractCoords(geom.coordinates);
    });
  });

  // Default extent if no coordinates found
  if (minX === Infinity) {
    minX = -180;
    maxX = 180;
    minY = -80;
    maxY = 80;
  }

  const rangeX = maxX - minX || 1.0;
  const rangeY = maxY - minY || 1.0;

  // Coordinate projection mapping (Lon/Lat -> SVG X/Y). Returns formatted
  // strings (not numbers) so the emitted attribute bytes match the Python twin
  // exactly (fmtNum strips trailing .0 -> `152` not `152.0`).
  const project = (coord: [number, number]): [string, string] => {
    const [lon, lat] = coord;
    const px = padding + ((lon - minX) / rangeX) * (width - padding * 2);
    // Invert Y axis for SVG top-down coordinates
    const py = height - padding - ((lat - minY) / rangeY) * (height - padding * 2);
    return [fmtNum(px), fmtNum(py)];
  };

  // 2. Render Layers to SVG paths
  let elementsSvg = "";
  const layers = mapspec?.layers || [];

  layers.forEach((layer: any) => {
    const srcId = layer.source;
    const src = sources[srcId];
    if (!src) return;

    const paint = layer.paint || {};
    const layerType = layer.type || "circle";

    if (layerType === "raster") {
      const opacity = escapeSvgAttr(fmtNum(Number(paint["raster-opacity"] ?? 1)));
      const tiles = src.tiles || (src.url ? [src.url] : []);
      const tileUrl = tiles[0] ? escapeSvgAttr(tiles[0]) : "";
      if (tileUrl) {
        // Oversample boost mirrors the Python twin and the exporter's
        // getOversampledZoom (log2(dpi/96), capped [0,2]) so a single formula
        // lives in one place. NOTE: this emits a *declarative* boost marker on
        // the <image>; the actual oversampled tile fetch is the separate #260
        // tile-rasterization-policy ticket and is not performed here.
        const zoomBoost = computeOversampleBoost(targetDpi);
        elementsSvg += `<image x="0" y="0" width="${width}" height="${height}" href="${tileUrl}" opacity="${opacity}" data-oversample-boost="${zoomBoost}" preserveAspectRatio="none" />\n`;
      }
      return;
    }

    if (!src.data) return;
    const features = src.data.type === "FeatureCollection" ? src.data.features : [src.data];

    features.forEach((feat: any) => {
      const geom = feat?.geometry;
      if (!geom) return;

      if (layerType === "circle" && geom.type === "Point") {
        const [x, y] = project(geom.coordinates as [number, number]);
        const baseRadius = Number(paint["circle-radius"] ?? 5);
        const radius = fmtNum(baseRadius * dpiScale);
        const color = escapeSvgAttr(paint["circle-color"] ?? "#3b82f6");
        const opacity = escapeSvgAttr(fmtNum(Number(paint["circle-opacity"] ?? 1)));

        elementsSvg += `<circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" fill-opacity="${opacity}" />\n`;
      } else if (layerType === "line" && (geom.type === "LineString" || geom.type === "MultiLineString")) {
        const lines = geom.type === "LineString" ? [geom.coordinates] : geom.coordinates;
        const baseWidth = Number(paint["line-width"] ?? 2);
        const lineWidth = fmtNum(baseWidth * dpiScale);
        const color = escapeSvgAttr(paint["line-color"] ?? "#2563eb");
        const opacity = escapeSvgAttr(fmtNum(Number(paint["line-opacity"] ?? 1)));

        lines.forEach((lineCoords: any) => {
          const pathPoints = lineCoords.map((c: any) => project(c).join(",")).join(" L ");
          elementsSvg += `<path d="M ${pathPoints}" stroke="${color}" stroke-width="${lineWidth}" stroke-opacity="${opacity}" fill="none" />\n`;
        });
      } else if (layerType === "fill" && (geom.type === "Polygon" || geom.type === "MultiPolygon")) {
        const polygons = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
        const color = escapeSvgAttr(paint["fill-color"] ?? "#60a5fa");
        const opacity = escapeSvgAttr(fmtNum(Number(paint["fill-opacity"] ?? 0.6)));
        const outlineColor = escapeSvgAttr(paint["fill-outline-color"] ?? "#1d4ed8");
        const outlineWidth = fmtNum(1.0 * dpiScale);

        polygons.forEach((polyRings: any) => {
          const outerRing = polyRings[0];
          if (!outerRing) return;
          const pointsStr = outerRing.map((c: any) => project(c).join(",")).join(" ");
          elementsSvg += `<polygon points="${pointsStr}" fill="${color}" fill-opacity="${opacity}" stroke="${outlineColor}" stroke-width="${outlineWidth}" />\n`;
        });
      } else if (layerType === "symbol" || layerType === "text") {
        const layout = layer.layout || {};
        const textFieldPattern = String(layout["text-field"] ?? paint["text-field"] ?? "{name}");
        const fieldName = textFieldPattern.replace(/^\{|\}$/g, "");
        const rawText = feat.properties?.[fieldName] ?? feat.properties?.name ?? feat.properties?.label ?? (textFieldPattern.startsWith("{") ? "" : textFieldPattern);
        if (!rawText) return;

        let coord: [number, number] | undefined;
        if (geom.type === "Point") {
          coord = geom.coordinates;
        } else if (geom.type === "LineString" && geom.coordinates.length > 0) {
          coord = geom.coordinates[Math.floor(geom.coordinates.length / 2)];
        } else if (geom.type === "Polygon" && geom.coordinates[0] && geom.coordinates[0].length > 0) {
          coord = geom.coordinates[0][0];
        }
        if (!coord) return;

        const [x, y] = project(coord as [number, number]);
        const baseSize = Number(layout["text-size"] ?? paint["text-size"] ?? 12);
        const fontSize = fmtNum(baseSize * dpiScale);
        const color = escapeSvgAttr(paint["text-color"] ?? layout["text-color"] ?? "#000000");
        const opacity = escapeSvgAttr(fmtNum(Number(paint["text-opacity"] ?? layout["text-opacity"] ?? 1)));
        const fontRaw = layout["text-font"] ?? paint["text-font"] ?? "sans-serif";
        const fontFamily = escapeSvgAttr(Array.isArray(fontRaw) ? fontRaw.join(", ") : fontRaw);

        let anchor = escapeSvgAttr(layout["text-anchor"] ?? paint["text-anchor"] ?? "middle");
        if (anchor === "center") anchor = "middle";
        else if (anchor === "left") anchor = "start";
        else if (anchor === "right") anchor = "end";

        const textEscaped = escapeSvgAttr(rawText);
        elementsSvg += `<text x="${x}" y="${y}" font-size="${fontSize}" font-family="${fontFamily}" fill="${color}" fill-opacity="${opacity}" text-anchor="${anchor}" dominant-baseline="central">${textEscaped}</text>\n`;
      }
    });
  });

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#ffffff" />
  <g class="mapspec-vector-layers">
    ${elementsSvg}
  </g>
</svg>`;
}
