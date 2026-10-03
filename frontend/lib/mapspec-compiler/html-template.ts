import { MapSpecLayoutConfig } from "./types";

/**
 * F-11: pinned MapLibre for the standalone render. Must stay on the same
 * major as the app (package.json maplibre-gl, >= 6.4.1 for CVE-2026-85061 /
 * #1341) and every CDN file carries Subresource Integrity. 6.x ships ESM
 * only: the entry + shared chunk are loaded through an import map whose
 * `integrity` table pins each module (the worker chunk is pinned too for
 * browsers that honour import-map integrity in workers).
 * Hashes = sha384 of node_modules/maplibre-gl@6.10.0/dist/* (verified equal to
 * the unpkg copies).
 */
export const MAPLIBRE_CDN_VERSION = "6.10.0";
const CDN = `https://unpkg.com/maplibre-gl@${MAPLIBRE_CDN_VERSION}/dist`;
export const MAPLIBRE_CDN_SRI: Record<string, string> = {
  "maplibre-gl.css": "sha384-Q5Blg3vUVAlUKqIPJYz7wGnz40Vwrx4pVuFVicI73+8c/26Zr5hhckfuIiIUflLE",
  "maplibre-gl.mjs": "sha384-2g0hrGNSeleJsCzq4bdDa1QEBwj09vTce/Hf9TbggAsMv37hy9xmKfJfCfOojhyx",
  "maplibre-gl-shared.mjs": "sha384-jw07Ono+c6G30wWc0ndm4f9k5wolLAdoHS5hC/WiNNVmg8Sfk4cJKfVGefVigjwA",
  "maplibre-gl-worker.mjs": "sha384-/TlJxJZJ5KRDZF4tXyO1GVYHjW5XZLAuo4P28ZFQ1TMEDA5eVxyw3zyDw6j5gMjV",
};

/**
 * F-11: serialize data for embedding inside an HTML `<script>` element.
 * `JSON.stringify` leaves `<` alone, so a feature property / label containing
 * `</script><script>…` would break out and execute. `\u003c` etc. are valid
 * JSON escapes, so JSON.parse round-trips the exact value.
 */
export function jsonForScript(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

export function generateMapHtml(style: object, layout?: MapSpecLayoutConfig): string {
  const styleJson = jsonForScript(style);
  const controlsJson = jsonForScript(layout?.controls ?? [{ type: "navigation", position: "top-right" }]);
  const importMap = jsonForScript({
    imports: { "maplibre-gl": `${CDN}/maplibre-gl.mjs` },
    integrity: Object.fromEntries(
      ["maplibre-gl.mjs", "maplibre-gl-shared.mjs", "maplibre-gl-worker.mjs"].map((f) => [
        `${CDN}/${f}`,
        MAPLIBRE_CDN_SRI[f],
      ]),
    ),
  });

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MapSpec Static Render</title>
  <link href="${CDN}/maplibre-gl.css" rel="stylesheet" integrity="${MAPLIBRE_CDN_SRI["maplibre-gl.css"]}" crossorigin="anonymous" />
  <script type="importmap">${importMap}</script>
  <style>
    body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #f8fafc; }
    #map { width: 100%; height: 100%; position: absolute; top: 0; bottom: 0; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script type="application/json" id="mapspec-style">${styleJson}</script>
  <script type="application/json" id="mapspec-controls">${controlsJson}</script>
  <script type="module">
    import * as maplibregl from "maplibre-gl";
    window.maplibregl = maplibregl;
    window.__MAPSPEC_STYLE__ = JSON.parse(document.getElementById("mapspec-style").textContent);
    window.__MAPSPEC_CONTROLS__ = JSON.parse(document.getElementById("mapspec-controls").textContent);
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
