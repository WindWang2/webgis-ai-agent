/**
 * 实验：MapLibre 在 map.setPixelRatio(k) 下，栅格瓦片源的取图 zoom 是否随 DPR 提升？
 * 用 addProtocol('probe', …) 捕获 MapLibre 实际请求的 {z}/{x}/{y} —— 无网络、确定性。
 * 结论进 docs/dev/ac-08-export-recon.md §6.2（P1 栅格 oversample 决策依据）。
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');
const MAPLIBRE_JS = path.join(FRONTEND_ROOT, 'node_modules', 'maplibre-gl', 'dist', 'maplibre-gl.js');

const html = `<!doctype html>
<html><head><meta charset="utf-8"><script src="file:///${MAPLIBRE_JS.replace(/\\/g, '/')}"></script>
<style>html,body{margin:0}#map{width:720px;height:480px}</style></head><body><div id="map"></div>
<script>
let requested = [];
window.__run = async function (dpr) {
  if (window.__map) { window.__map.remove(); window.__map = null; }
  requested = [];
  maplibregl.addProtocol('probe', async (params) => {
    requested.push(params.url);
    // 1x1 透明 PNG
    const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='), (c) => c.charCodeAt(0));
    return { data: bytes.buffer };
  });
  const map = new maplibregl.Map({
    container: 'map', center: [0, 0], zoom: 5, pixelRatio: dpr, interactive: false,
    attributionControl: false,
    style: {
      version: 8,
      sources: { t: { type: 'raster', tiles: ['probe://t/{z}/{x}/{y}'], tileSize: 256 } },
      layers: [{ id: 'r', type: 'raster', source: 't' }],
    },
  });
  window.__map = map;
  await new Promise((res, rej) => { map.on('load', res); map.on('error', (e) => rej(new Error(e.error && e.error.message))); });
  await map.once('idle');
  const zs = [...new Set(requested.map((u) => parseInt(u.match(/probe:\\/\\/t\\/(\\d+)\\//)[1], 10)))].sort((a, b) => a - b);
  return { dpr, zoomLevels: zs, requestCount: requested.length };
};
</script></body></html>`;

const browser = await chromium.launch({ args: ['--use-angle=swiftshader'] });
try {
  const htmlPath = path.join(FRONTEND_ROOT, '.ac08-tilezoom-probe.html');
  fs.writeFileSync(htmlPath, html);
  const page = await browser.newPage();
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'));
  const r1 = await page.evaluate(() => window.__run(1));
  const r2 = await page.evaluate(() => window.__run(3.125));
  console.log('DPR 1     →', JSON.stringify(r1));
  console.log('DPR 3.125 →', JSON.stringify(r2));
  console.log((r1.zoomLevels.join(',') === r2.zoomLevels.join(','))
    ? 'VERDICT: tile zoom 不随 DPR 变化 → 高 DPI 下栅格瓦片确无细节增益，需要显式 oversample 或矢量优先。'
    : 'VERDICT: tile zoom 随 DPR 自动提升 → 栅格细节增益已内建。');
  fs.rmSync(htmlPath, { force: true });
} finally {
  await browser.close();
}
