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

import { computeOversampleBoost, resolveOversampledTileGrid, mercY } from "../map-kit/oversample";

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

function parseColor(c: any): [number, number, number] | null {
  if (typeof c !== "string") return null;
  const s = c.trim().toLowerCase();
  if (s.startsWith("#")) {
    const hex = s.slice(1);
    if (hex.length === 3) {
      return [
        parseInt(hex[0] + hex[0], 16),
        parseInt(hex[1] + hex[1], 16),
        parseInt(hex[2] + hex[2], 16),
      ];
    } else if (hex.length === 6 || hex.length === 8) {
      return [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
      ];
    }
  } else if (s.startsWith("rgb")) {
    const m = s.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    if (m) {
      return [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
    }
  }
  return null;
}

function rgbToHex(r: number, g: number, b: number): string {
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  const hR = clamp(r).toString(16).padStart(2, "0");
  const hG = clamp(g).toString(16).padStart(2, "0");
  const hB = clamp(b).toString(16).padStart(2, "0");
  return `#${hR}${hG}${hB}`;
}

function interpolateValue(v0: any, v1: any, t: number): any {
  const n0 = Number(v0);
  const n1 = Number(v1);
  if (Number.isFinite(n0) && Number.isFinite(n1)) {
    return n0 + t * (n1 - n0);
  }
  const c0 = parseColor(v0);
  const c1 = parseColor(v1);
  if (c0 && c1) {
    const r = c0[0] + t * (c1[0] - c0[0]);
    const g = c0[1] + t * (c1[1] - c0[1]);
    const b = c0[2] + t * (c1[2] - c0[2]);
    return rgbToHex(r, g, b);
  }
  return t < 0.5 ? v0 : v1;
}

/**
 * Resolves MapSpec paint values (primitives or StyleMethod objects like constant, field, match, step, interpolate).
 */
export function resolvePaintValue(
  val: any,
  props?: Record<string, any>,
  fallback?: any
): any {
  if (val === undefined || val === null) {
    return fallback;
  }
  if (typeof val !== "object" || Array.isArray(val)) {
    return val;
  }

  const method = val.method;
  if (!method || typeof method !== "string") {
    return val;
  }

  if (method === "constant") {
    return val.value !== undefined ? val.value : fallback;
  }

  if (method === "field") {
    const f = val.field;
    if (props && f && props[f] !== undefined && props[f] !== null) {
      return props[f];
    }
    return fallback;
  }

  if (method === "match") {
    const f = val.field;
    const propVal = props && f ? props[f] : undefined;
    const cases = val.cases || [];
    if (propVal !== undefined && propVal !== null) {
      for (const pair of cases) {
        if (Array.isArray(pair) && pair.length >= 2) {
          if (String(propVal) === String(pair[0]) || propVal === pair[0]) {
            return pair[1];
          }
        }
      }
    }
    return val.default !== undefined ? val.default : fallback;
  }

  if (method === "step") {
    const f = val.field;
    const propVal = props && f ? Number(props[f]) : NaN;
    const stops = val.stops || [];
    if (!Array.isArray(stops) || stops.length === 0) {
      return val.default !== undefined ? val.default : fallback;
    }
    let res = val.default !== undefined ? val.default : stops[0][1];
    if (Number.isFinite(propVal)) {
      for (const stop of stops) {
        if (Array.isArray(stop) && stop.length >= 2) {
          const thresh = Number(stop[0]);
          if (Number.isFinite(thresh) && propVal >= thresh) {
            res = stop[1];
          }
        }
      }
    }
    return res;
  }

  if (method === "interpolate") {
    const f = val.field;
    const propVal = props && f ? Number(props[f]) : NaN;
    const stops = val.stops || [];
    if (!Array.isArray(stops) || stops.length === 0) {
      return val.default !== undefined ? val.default : fallback;
    }
    if (!Number.isFinite(propVal)) {
      return stops[0][1] !== undefined ? stops[0][1] : fallback;
    }

    const firstStop = stops[0];
    const lastStop = stops[stops.length - 1];
    if (propVal <= Number(firstStop[0])) {
      return firstStop[1];
    }
    if (propVal >= Number(lastStop[0])) {
      return lastStop[1];
    }

    for (let i = 0; i < stops.length - 1; i++) {
      const s0 = stops[i];
      const s1 = stops[i + 1];
      const x0 = Number(s0[0]);
      const x1 = Number(s1[0]);
      if (propVal >= x0 && propVal <= x1) {
        if (x1 === x0) return s0[1];
        const t = (propVal - x0) / (x1 - x0);
        return interpolateValue(s0[1], s1[1], t);
      }
    }
    return lastStop[1];
  }

  return val.value !== undefined ? val.value : fallback;
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

  const scaledWidth = width * dpiScale;
  const scaledHeight = height * dpiScale;
  const scaledPadding = padding * dpiScale;

  // 1. Gather all coordinates across sources to compute extent bounding box
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;

  const sources = mapspec?.sources || {};
  Object.values(sources).forEach((src: any) => {
    const geojson = src?.inlineData ?? src?.data;
    if (!geojson) return;
    const features = geojson.type === "FeatureCollection" ? geojson.features : [geojson];

    features.forEach((feat: any) => {
      const geom = feat?.geometry;
      if (!geom) return;

      const extractCoords = (c: any) => {
        if (
          Array.isArray(c) &&
          c.length >= 2 &&
          typeof c[0] === "number" &&
          typeof c[1] === "number" &&
          Number.isFinite(c[0]) &&
          Number.isFinite(c[1])
        ) {
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

  // Default extent if no valid coordinates found or NaN/Inf bounds
  if (
    !Number.isFinite(minX) ||
    !Number.isFinite(maxX) ||
    !Number.isFinite(minY) ||
    !Number.isFinite(maxY) ||
    maxX < minX ||
    maxY < minY
  ) {
    minX = -180;
    maxX = 180;
    minY = -80;
    maxY = 80;
  }

  const rangeX = maxX - minX || 1.0;

  const mercMinY = mercY(minY);
  const mercMaxY = mercY(maxY);
  const rangeMercY = (mercMaxY - mercMinY) || 1.0;

  // Coordinate projection mapping (Lon/Lat -> SVG X/Y). Returns formatted
  // strings (not numbers) so the emitted attribute bytes match the Python twin
  // exactly (fmtNum strips trailing .0 -> `152` not `152.0`).
  // Coordinate projection mapping (Lon/Lat -> SVG X/Y). Returns formatted
  // strings (not numbers) so the emitted attribute bytes match the Python twin
  // exactly (fmtNum strips trailing .0 -> `152` not `152.0`).
  const project = (coord: [number, number]): [string, string] => {
    let [lon, lat] = coord || [0, 0];
    if (typeof lon !== "number" || !Number.isFinite(lon)) lon = 0;
    if (typeof lat !== "number" || !Number.isFinite(lat)) lat = 0;
    let px = scaledPadding + ((lon - minX) / rangeX) * (scaledWidth - scaledPadding * 2);
    const normY = (mercY(lat) - mercMinY) / rangeMercY;
    let py = scaledHeight - scaledPadding - normY * (scaledHeight - scaledPadding * 2);
    if (!Number.isFinite(px)) px = 0;
    if (!Number.isFinite(py)) py = 0;
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
      const rawOpacity = resolvePaintValue(paint["raster-opacity"] ?? paint["opacity"], undefined, 1);
      const opacity = escapeSvgAttr(fmtNum(Number(rawOpacity ?? 1)));
      const tiles = src.tiles || (src.url ? [src.url] : []);
      const tileUrl = tiles[0] || "";
      if (tileUrl) {
        const zoomBoost = computeOversampleBoost(targetDpi);
        const tileItems = resolveOversampledTileGrid({
          bounds: [minX, minY, maxX, maxY],
          width: scaledWidth,
          height: scaledHeight,
          padding: scaledPadding,
          targetDpi,
          tileUrlTemplate: tileUrl,
        });

        for (const item of tileItems) {
          const itemUrl = escapeSvgAttr(item.url);
          const px = fmtNum(item.x);
          const py = fmtNum(item.y);
          const pw = fmtNum(item.width);
          const ph = fmtNum(item.height);
          elementsSvg += `<image x="${px}" y="${py}" width="${pw}" height="${ph}" href="${itemUrl}" opacity="${opacity}" data-oversample-boost="${zoomBoost}" preserveAspectRatio="none" />\n`;
        }
      }
      return;
    }

    const srcData = src.inlineData ?? src.data;
    if (!srcData) return;
    const features = srcData.type === "FeatureCollection" ? srcData.features : [srcData];

    features.forEach((feat: any) => {
      const geom = feat?.geometry;
      if (!geom) return;
      const props = feat?.properties || {};

      if (layerType === "circle" && geom.type === "Point") {
        const [x, y] = project(geom.coordinates as [number, number]);
        const baseRadius = Number(resolvePaintValue(paint["circle-radius"] ?? paint["radius"], props, 5));
        const radius = fmtNum(baseRadius * dpiScale);
        const color = escapeSvgAttr(resolvePaintValue(paint["circle-color"] ?? paint["color"], props, "#3b82f6"));
        const opacity = escapeSvgAttr(fmtNum(Number(resolvePaintValue(paint["circle-opacity"] ?? paint["opacity"], props, 1))));

        const baseStrokeWidth = Number(resolvePaintValue(paint["circle-stroke-width"] ?? paint["stroke-width"] ?? paint["strokeWidth"], props, 0));
        let strokeAttr = "";
        if (baseStrokeWidth > 0) {
          const strokeWidth = fmtNum(baseStrokeWidth * dpiScale);
          const strokeColor = escapeSvgAttr(resolvePaintValue(paint["circle-stroke-color"] ?? paint["stroke-color"] ?? paint["strokeColor"], props, "#000000"));
          const strokeOpacity = escapeSvgAttr(fmtNum(Number(resolvePaintValue(paint["circle-stroke-opacity"] ?? paint["stroke-opacity"] ?? paint["strokeOpacity"], props, 1))));
          strokeAttr = ` stroke="${strokeColor}" stroke-width="${strokeWidth}" stroke-opacity="${strokeOpacity}"`;
        }

        elementsSvg += `<circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" fill-opacity="${opacity}"${strokeAttr} />\n`;
      } else if (layerType === "line" && (geom.type === "LineString" || geom.type === "MultiLineString")) {
        const layout = layer.layout || {};
        const lines = geom.type === "LineString" ? [geom.coordinates] : geom.coordinates;
        const baseWidth = Number(resolvePaintValue(paint["line-width"] ?? paint["width"], props, 2));
        const lineWidth = fmtNum(baseWidth * dpiScale);
        const color = escapeSvgAttr(resolvePaintValue(paint["line-color"] ?? paint["color"], props, "#2563eb"));
        const opacity = escapeSvgAttr(fmtNum(Number(resolvePaintValue(paint["line-opacity"] ?? paint["opacity"], props, 1))));

        let extraAttrs = "";
        const linecap = resolvePaintValue(layout["line-linecap"] ?? paint["line-linecap"] ?? layout["lineCap"] ?? paint["lineCap"], props, null);
        if (linecap && typeof linecap === "string") {
          extraAttrs += ` stroke-linecap="${escapeSvgAttr(linecap)}"`;
        }

        const linejoin = resolvePaintValue(layout["line-linejoin"] ?? paint["line-linejoin"] ?? layout["lineJoin"] ?? paint["lineJoin"], props, null);
        if (linejoin && typeof linejoin === "string") {
          extraAttrs += ` stroke-linejoin="${escapeSvgAttr(linejoin)}"`;
        }

        const rawDash = resolvePaintValue(paint["line-dasharray"] ?? layout["line-dasharray"] ?? paint["dasharray"], props, null);
        if (rawDash !== null && rawDash !== undefined) {
          let dashStr = "";
          if (Array.isArray(rawDash)) {
            dashStr = rawDash.map((v: any) => fmtNum(Number(v) * dpiScale)).join(",");
          } else if (typeof rawDash === "string" && rawDash.trim()) {
            const parts = rawDash.trim().split(/[\s,]+/).filter(Boolean);
            try {
              dashStr = parts.map((p: string) => fmtNum(Number(p) * dpiScale)).join(",");
            } catch {
              dashStr = escapeSvgAttr(rawDash);
            }
          }
          if (dashStr) {
            extraAttrs += ` stroke-dasharray="${dashStr}"`;
          }
        }

        lines.forEach((lineCoords: any) => {
          if (!Array.isArray(lineCoords) || lineCoords.length === 0) return;
          const pathPoints = lineCoords.map((c: any) => project(c).join(",")).join(" L ");
          elementsSvg += `<path d="M ${pathPoints}" stroke="${color}" stroke-width="${lineWidth}" stroke-opacity="${opacity}" fill="none"${extraAttrs} />\n`;
        });
      } else if ((layerType === "fill" || layerType === "fill-extrusion") && (geom.type === "Polygon" || geom.type === "MultiPolygon")) {
        const polygons = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
        const defaultColor = layerType === "fill-extrusion" ? "#94a3b8" : "#60a5fa";
        const defaultOpacity = layerType === "fill-extrusion" ? 0.8 : 0.6;
        const defaultOutline = layerType === "fill-extrusion" ? "#475569" : "#1d4ed8";

        const color = escapeSvgAttr(resolvePaintValue(paint["fill-extrusion-color"] ?? paint["fill-color"] ?? paint["color"], props, defaultColor));
        const opacity = escapeSvgAttr(fmtNum(Number(resolvePaintValue(paint["fill-extrusion-opacity"] ?? paint["fill-opacity"] ?? paint["opacity"], props, defaultOpacity))));
        const outlineColor = escapeSvgAttr(resolvePaintValue(paint["fill-extrusion-base-color"] ?? paint["fill-outline-color"] ?? paint["fill-outline"] ?? paint["strokeColor"], props, defaultOutline));
        const outlineWidth = fmtNum(1.0 * dpiScale);
        let extraAttrs = "";
        if (layerType === "fill-extrusion") {
          extraAttrs += ' data-export-degraded="true" data-export-degraded-reason="3d_perspective_not_vectorized"';
        }

        polygons.forEach((polyRings: any) => {
          if (!Array.isArray(polyRings) || polyRings.length === 0) return;
          const ringPaths: string[] = [];
          polyRings.forEach((ring: any) => {
            if (!Array.isArray(ring) || ring.length === 0) return;
            const pts = ring.map((c: any) => project(c).join(",")).join(" L ");
            ringPaths.push(`M ${pts} Z`);
          });
          if (ringPaths.length === 0) return;
          const dStr = ringPaths.join(" ");
          elementsSvg += `<path d="${dStr}" fill="${color}" fill-opacity="${opacity}" fill-rule="evenodd" stroke="${outlineColor}" stroke-width="${outlineWidth}"${extraAttrs} />\n`;
        });
      } else if (layerType === "heatmap") {
        const pts: [number, number][] = [];
        if (geom.type === "Point" && Array.isArray(geom.coordinates)) {
          pts.push(geom.coordinates as [number, number]);
        } else if (geom.type === "MultiPoint" && Array.isArray(geom.coordinates)) {
          pts.push(...(geom.coordinates as [number, number][]));
        } else if (geom.type === "LineString" && Array.isArray(geom.coordinates)) {
          pts.push(...(geom.coordinates as [number, number][]));
        } else if (geom.type === "Polygon" && Array.isArray(geom.coordinates) && geom.coordinates[0]) {
          pts.push(...(geom.coordinates[0] as [number, number][]));
        }

        const baseRadius = Number(resolvePaintValue(paint["heatmap-radius"] ?? paint["radius"] ?? paint["circle-radius"], props, 15));
        const radius = fmtNum(baseRadius * dpiScale);
        const color = escapeSvgAttr(resolvePaintValue(paint["heatmap-color"] ?? paint["color"], props, "#ef4444"));
        const opacity = escapeSvgAttr(fmtNum(Number(resolvePaintValue(paint["heatmap-opacity"] ?? paint["opacity"], props, 0.6))));

        pts.forEach((pt) => {
          if (!Array.isArray(pt) || pt.length < 2) return;
          const [x, y] = project(pt);
          elementsSvg += `<circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" fill-opacity="${opacity}" />\n`;
        });
      } else if (layerType === "symbol" || layerType === "text") {
        const layout = layer.layout || {};
        const textFieldPattern = String(layout["text-field"] ?? paint["text-field"] ?? "{name}");
        const fieldName = textFieldPattern.replace(/^\{|\}$/g, "");
        const rawText = props?.[fieldName] ?? props?.name ?? props?.label ?? (textFieldPattern.startsWith("{") ? "" : textFieldPattern);
        if (!rawText) return;

        let coord: [number, number] | undefined;
        if (geom.type === "Point") {
          coord = geom.coordinates;
        } else if (geom.type === "LineString" && geom.coordinates.length > 0) {
          coord = geom.coordinates[Math.floor(geom.coordinates.length / 2)];
        } else if ((geom.type === "Polygon" || geom.type === "MultiPolygon") && Array.isArray(geom.coordinates)) {
          let pMinX = Infinity, pMaxX = -Infinity, pMinY = Infinity, pMaxY = -Infinity;
          const extractRing = (ring: any) => {
            if (Array.isArray(ring)) {
              ring.forEach((c: any) => {
                if (Array.isArray(c) && c.length >= 2 && typeof c[0] === "number" && typeof c[1] === "number" && Number.isFinite(c[0]) && Number.isFinite(c[1])) {
                  pMinX = Math.min(pMinX, c[0]);
                  pMaxX = Math.max(pMaxX, c[0]);
                  pMinY = Math.min(pMinY, c[1]);
                  pMaxY = Math.max(pMaxY, c[1]);
                } else if (Array.isArray(c)) {
                  extractRing(c);
                }
              });
            }
          };
          extractRing(geom.coordinates);
          if (Number.isFinite(pMinX) && Number.isFinite(pMaxX) && Number.isFinite(pMinY) && Number.isFinite(pMaxY)) {
            coord = [(pMinX + pMaxX) / 2, (pMinY + pMaxY) / 2];
          }
        }
        if (!coord) return;

        const [x, y] = project(coord as [number, number]);
        const baseSize = Number(resolvePaintValue(layout["text-size"] ?? paint["text-size"] ?? layout["labelSize"] ?? paint["labelSize"], props, 12));
        const fontSize = fmtNum(baseSize * dpiScale);
        const color = escapeSvgAttr(resolvePaintValue(paint["text-color"] ?? layout["text-color"] ?? paint["labelColor"] ?? layout["labelColor"], props, "#000000"));
        const opacity = escapeSvgAttr(fmtNum(Number(resolvePaintValue(paint["text-opacity"] ?? layout["text-opacity"] ?? paint["labelOpacity"] ?? layout["labelOpacity"], props, 1))));
        const fontRaw = layout["text-font"] ?? paint["text-font"] ?? "sans-serif";
        const fontFamily = escapeSvgAttr(Array.isArray(fontRaw) ? fontRaw.join(", ") : fontRaw);

        const rawAnchor = String(layout["text-anchor"] ?? paint["text-anchor"] ?? "center");
        let svgTextAnchor = "middle";
        let svgDominantBaseline = "central";

        switch (rawAnchor) {
          case "top":
            svgTextAnchor = "middle";
            svgDominantBaseline = "hanging";
            break;
          case "bottom":
            svgTextAnchor = "middle";
            svgDominantBaseline = "ideographic";
            break;
          case "left":
          case "start":
            svgTextAnchor = "start";
            svgDominantBaseline = "central";
            break;
          case "right":
          case "end":
            svgTextAnchor = "end";
            svgDominantBaseline = "central";
            break;
          case "top-left":
            svgTextAnchor = "start";
            svgDominantBaseline = "hanging";
            break;
          case "top-right":
            svgTextAnchor = "end";
            svgDominantBaseline = "hanging";
            break;
          case "bottom-left":
            svgTextAnchor = "start";
            svgDominantBaseline = "ideographic";
            break;
          case "bottom-right":
            svgTextAnchor = "end";
            svgDominantBaseline = "ideographic";
            break;
          case "center":
          case "middle":
          default:
            svgTextAnchor = "middle";
            svgDominantBaseline = "central";
            break;
        }

        const textEscaped = escapeSvgAttr(rawText);

        const baseHaloWidth = Number(resolvePaintValue(paint["text-halo-width"] ?? layout["text-halo-width"] ?? paint["haloWidth"] ?? layout["haloWidth"] ?? paint["textHaloWidth"], props, 0));
        if (baseHaloWidth > 0) {
          const haloWidth = fmtNum(baseHaloWidth * dpiScale * 2);
          const haloColor = escapeSvgAttr(resolvePaintValue(paint["text-halo-color"] ?? layout["text-halo-color"] ?? paint["haloColor"] ?? layout["haloColor"] ?? paint["textHaloColor"], props, "#ffffff"));
          const haloOpacityVal = resolvePaintValue(paint["text-halo-opacity"] ?? layout["text-halo-opacity"] ?? paint["haloOpacity"] ?? layout["haloOpacity"], props, 1);
          const haloOpacity = escapeSvgAttr(fmtNum(Number(haloOpacityVal)));
          elementsSvg += `<text x="${x}" y="${y}" font-size="${fontSize}" font-family="${fontFamily}" fill="none" stroke="${haloColor}" stroke-width="${haloWidth}" stroke-opacity="${haloOpacity}" stroke-linejoin="round" stroke-linecap="round" text-anchor="${svgTextAnchor}" dominant-baseline="${svgDominantBaseline}">${textEscaped}</text>\n`;
        }

        elementsSvg += `<text x="${x}" y="${y}" font-size="${fontSize}" font-family="${fontFamily}" fill="${color}" fill-opacity="${opacity}" text-anchor="${svgTextAnchor}" dominant-baseline="${svgDominantBaseline}">${textEscaped}</text>\n`;
      }
    });
  });

  const viewBoxW = fmtNum(scaledWidth);
  const viewBoxH = fmtNum(scaledHeight);

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${viewBoxW} ${viewBoxH}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#ffffff" />
  <g class="mapspec-vector-layers">
    ${elementsSvg}
  </g>
</svg>`;
}

