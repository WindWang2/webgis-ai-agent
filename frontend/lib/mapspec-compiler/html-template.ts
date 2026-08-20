import { MapSpecLayoutConfig } from "./types";

export function generateMapHtml(style: any, layout?: MapSpecLayoutConfig): string {
  const styleJson = JSON.stringify(style);
  const controlsJson = JSON.stringify(layout?.controls ?? [{ type: "navigation", position: "top-right" }]);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MapSpec Static Render</title>
  <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
  <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
  <style>
    body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #f8fafc; }
    #map { width: 100%; height: 100%; position: absolute; top: 0; bottom: 0; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    window.__MAPSPEC_STYLE__ = ${styleJson};
    window.__MAPSPEC_CONTROLS__ = ${controlsJson};
    const __resolvedStyle = JSON.parse(JSON.stringify(window.__MAPSPEC_STYLE__).replaceAll("__ORIGIN__", location.origin));

    const map = new maplibregl.Map({
      container: 'map',
      style: __resolvedStyle,
      center: __resolvedStyle.center || [0, 0],
      zoom: __resolvedStyle.zoom || 2,
    });
    window.__MAP__ = map;

    window.__MAP_LOADED__ = false;
    window.__MAP_IDLE__ = false;

    map.on('load', () => {
      window.__MAP_LOADED__ = true;
      document.body.setAttribute('data-webgis-loaded', 'true');
    });

    map.on('idle', () => {
      window.__MAP_IDLE__ = true;
      document.body.setAttribute('data-webgis-idle', 'true');
    });

    window.__MAPSPEC_CONTROLS__.forEach(ctrl => {
      if (ctrl.type === 'navigation') {
        map.addControl(new maplibregl.NavigationControl(), ctrl.position || 'top-right');
      } else if (ctrl.type === 'scale') {
        map.addControl(new maplibregl.ScaleControl(), ctrl.position || 'bottom-left');
      } else if (ctrl.type === 'fullscreen') {
        map.addControl(new maplibregl.FullscreenControl(), ctrl.position || 'top-right');
      }
    });
  </script>
</body>
</html>`;
}
