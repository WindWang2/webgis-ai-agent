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

  // Coordinate projection mapping (Lon/Lat -> SVG X/Y)
  const project = (coord: [number, number]): [number, number] => {
    const [lon, lat] = coord;
    const px = padding + ((lon - minX) / rangeX) * (width - padding * 2);
    // Invert Y axis for SVG top-down coordinates
    const py = height - padding - ((lat - minY) / rangeY) * (height - padding * 2);
    return [Math.round(px * 100) / 100, Math.round(py * 100) / 100];
  };

  // 2. Render Layers to SVG paths
  let elementsSvg = "";
  const layers = mapspec?.layers || [];

  layers.forEach((layer: any) => {
    const srcId = layer.source;
    const src = sources[srcId];
    if (!src || !src.data) return;

    const paint = layer.paint || {};
    const layerType = layer.type || "circle";
    const features = src.data.type === "FeatureCollection" ? src.data.features : [src.data];

    features.forEach((feat: any) => {
      const geom = feat?.geometry;
      if (!geom) return;

      if (layerType === "circle" && geom.type === "Point") {
        const [x, y] = project(geom.coordinates as [number, number]);
        const baseRadius = Number(paint["circle-radius"] ?? 5);
        const radius = Math.round(baseRadius * dpiScale * 100) / 100;
        const color = paint["circle-color"] ?? "#3b82f6";
        const opacity = paint["circle-opacity"] ?? 1.0;

        elementsSvg += `<circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" fill-opacity="${opacity}" />\n`;
      } else if (layerType === "line" && (geom.type === "LineString" || geom.type === "MultiLineString")) {
        const lines = geom.type === "LineString" ? [geom.coordinates] : geom.coordinates;
        const baseWidth = Number(paint["line-width"] ?? 2);
        const lineWidth = Math.round(baseWidth * dpiScale * 100) / 100;
        const color = paint["line-color"] ?? "#2563eb";
        const opacity = paint["line-opacity"] ?? 1.0;

        lines.forEach((lineCoords: any) => {
          const pathPoints = lineCoords.map((c: any) => project(c).join(",")).join(" L ");
          elementsSvg += `<path d="M ${pathPoints}" stroke="${color}" stroke-width="${lineWidth}" stroke-opacity="${opacity}" fill="none" />\n`;
        });
      } else if (layerType === "fill" && (geom.type === "Polygon" || geom.type === "MultiPolygon")) {
        const polygons = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
        const color = paint["fill-color"] ?? "#60a5fa";
        const opacity = paint["fill-opacity"] ?? 0.6;
        const outlineColor = paint["fill-outline-color"] ?? "#1d4ed8";

        polygons.forEach((polyRings: any) => {
          const outerRing = polyRings[0];
          if (!outerRing) return;
          const pointsStr = outerRing.map((c: any) => project(c).join(",")).join(" ");
          elementsSvg += `<polygon points="${pointsStr}" fill="${color}" fill-opacity="${opacity}" stroke="${outlineColor}" stroke-width="1" />\n`;
        });
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
