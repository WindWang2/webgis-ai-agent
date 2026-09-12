/**
 * ac-08 出版级导出 · DPI 线宽质量探针（P0 基线 / P7 验收共用工具）。
 *
 * 度量同一 MapSpec（递减线宽的水平线行组）在三条路径下的「可分辨最小线宽」：
 *   A. true-rerender  —— map.setPixelRatio(dpi/96) 真重渲染后读 canvas（生产路径）
 *   B. upscale        —— DPR=1 渲染后 drawImage 插值放大到同尺寸（旧路径/降级面）
 *   C. baseline96     —— 96 DPI 原生渲染（参照系）
 *
 * 无网络依赖（inline GeoJSON，白底黑线）、单页小尺寸、串行单实例 —— 符合
 * 任务书 §0.4 导出实测资源纪律。
 *
 * 用法（frontend/ 下）：
 *   node scripts/ac08/dpi-line-probe.mjs --dpi 96,150,300 \
 *     --out ../../docs/dev/ac-08-samples/dpi-baseline
 *
 * 「可分辨」判定：线带相对背景的亮度对比度 ≥ 0.10（10% 亮度凹陷）。
 * 有效线宽 = 亮度剖面半高宽（FWHM，换算回 CSS px）。
 */
import { chromium } from 'playwright';
import { PNG } from 'pngjs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');
const MAPLIBRE_JS = path.join(FRONTEND_ROOT, 'node_modules', 'maplibre-gl', 'dist', 'maplibre-gl.js');
const MAPLIBRE_CSS = path.join(FRONTEND_ROOT, 'node_modules', 'maplibre-gl', 'dist', 'maplibre-gl.css');

const WIDTHS_CSS_PX = [0.25, 0.5, 0.75, 1, 1.5, 2, 3];
const VIEW_W = 720;
const VIEW_H = 480;
const RESOLVABLE_CONTRAST = 0.1;

function pageHtml() {
  return `<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="file:///${MAPLIBRE_CSS.replace(/\\/g, '/')}">
<script src="file:///${MAPLIBRE_JS.replace(/\\/g, '/')}"></script>
<style>html,body{margin:0;padding:0}#map{width:${VIEW_W}px;height:${VIEW_H}px}</style>
</head><body>
<div id="map"></div>
<script>
  const VIEW_W = ${VIEW_W};
  const VIEW_H = ${VIEW_H};
  const WIDTHS = ${JSON.stringify(WIDTHS_CSS_PX)};
  const rows = [];
  for (let i = 0; i < WIDTHS.length; i++) {
    const y = 40 + i * 60;
    rows.push({ width: WIDTHS[i], y });
  }
  function latForY(map, yPx) {
    return map.unproject([VIEW_W / 2, yPx]).lat;
  }
  async function buildMap(pixelRatio) {
    const map = new maplibregl.Map({
      container: 'map',
      center: [0, 0],
      zoom: 1,
      pixelRatio,
      interactive: false,
      attributionControl: false,
      style: { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#ffffff' } }] },
    });
    await new Promise((resolve, reject) => {
      map.on('load', resolve);
      map.on('error', (e) => reject(new Error('maplibre error: ' + (e && e.error && e.error.message || e))));
    });
    const features = rows.map((r) => ({
      type: 'Feature',
      properties: { w: r.width },
      geometry: { type: 'LineString', coordinates: [[0, 0], [0, 0]] },
    }));
    // 逐行先 unproject 定纬度，再回填坐标（zoom 已定，unproject 稳定）。
    features.forEach((f, i) => {
      const lat = latForY(map, rows[i].y);
      f.geometry.coordinates = [[-60, lat], [60, lat]];
    });
    map.addSource('lines', { type: 'geojson', data: { type: 'FeatureCollection', features } });
    rows.forEach((r) => {
      map.addLayer({
        id: 'line-' + r.width,
        type: 'line',
        source: 'lines',
        filter: ['==', ['get', 'w'], r.width],
        layout: { 'line-cap': 'butt' },
        paint: { 'line-color': '#000000', 'line-width': r.width },
      });
    });
    await map.once('idle');
    return map;
  }
  function captureDataUrl(map) {
    // preserveDrawingBuffer 默认 false —— 渲染后同步读 getCanvas() 需要
    // 在 render 回调内取；这里用 maplibre 的 triggerRepaint + once('render')。
    return new Promise((resolve) => {
      map.once('render', () => resolve(map.getCanvas().toDataURL('image/png')));
      map.triggerRepaint();
    });
  }
  function upscaleDataUrl(dataUrl, scale) {
    const img = new Image();
    return new Promise((resolve, reject) => {
      img.onload = () => {
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * scale);
        c.height = Math.round(img.height * scale);
        const ctx = c.getContext('2d');
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL('image/png'));
      };
      img.onerror = (e) => reject(new Error('img load failed'));
      img.src = dataUrl;
    });
  }
  window.__probe = {
    rows,
    buildMap,
    captureDataUrl,
    upscaleDataUrl,
  };
</script>
</body></html>`;
}

function luminance(r, g, b) {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/** 对一张 PNG 按行组测剖面：返回每名义线宽的 {contrast, effectiveWidthCssPx}。 */
function measureRow(png, rows, deviceScale) {
  const { width, height, data } = png;
  const results = [];
  for (const row of rows) {
    const centerDev = Math.round(row.y * deviceScale);
    const winDev = Math.max(4, Math.round(8 * deviceScale));
    const x0 = Math.round(width * 0.25);
    const x1 = Math.round(width * 0.75);
    const profile = [];
    for (let y = centerDev - winDev; y <= centerDev + winDev; y++) {
      if (y < 0 || y >= height) {
        profile.push(255);
        continue;
      }
      let sum = 0;
      let n = 0;
      for (let x = x0; x < x1; x++) {
        const idx = (y * width + x) * 4;
        sum += luminance(data[idx], data[idx + 1], data[idx + 2]);
        n++;
      }
      profile.push(sum / n);
    }
    // 背景 = 窗口边缘 25% 分位（抗黑线污染）
    const edges = [...profile.slice(0, Math.round(profile.length * 0.2)), ...profile.slice(-Math.round(profile.length * 0.2))];
    const sortedEdges = [...edges].sort((a, b) => a - b);
    const bg = sortedEdges[Math.floor(sortedEdges.length / 2)];
    const minVal = Math.min(...profile);
    const contrast = (bg - minVal) / bg;
    // FWHM：以 (bg+min)/2 为阈的连续过阈段总长
    const half = (bg + minVal) / 2;
    let fwhmDev = 0;
    let inBand = false;
    for (const v of profile) {
      if (v < half) {
        fwhmDev++;
        inBand = true;
      } else if (inBand) {
        // 允许 1px 噪声断裂
        let noise = true;
        const idxNext = profile.indexOf(v);
        for (let k = 1; k <= 1; k++) {
          if (idxNext + k < profile.length && profile[idxNext + k] < half) noise = false;
        }
        if (noise) inBand = false;
      }
    }
    results.push({
      nominalCssPx: row.width,
      contrast: Number(contrast.toFixed(4)),
      effectiveWidthCssPx: Number((fwhmDev / deviceScale).toFixed(3)),
      resolvable: contrast >= RESOLVABLE_CONTRAST,
    });
  }
  return results;
}

function decodePng(dataUrl) {
  const base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
  return PNG.sync.read(Buffer.from(base64, 'base64'));
}

async function main() {
  const args = process.argv.slice(2);
  const dpiArg = (args.find((a) => a.startsWith('--dpi')) || '--dpi 96,300').split('=')[1] || args[args.indexOf('--dpi') + 1] || '96,300';
  const dpis = dpiArg.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));
  const outIdx = args.indexOf('--out');
  const outDir = path.resolve(outIdx >= 0 ? args[outIdx + 1] : path.join(__dirname, '..', '..', '..', 'docs', 'dev', 'ac-08-samples', 'dpi-baseline'));
  fs.mkdirSync(outDir, { recursive: true });

  const htmlPath = path.join(FRONTEND_ROOT, '.ac08-probe.html');
  fs.writeFileSync(htmlPath, pageHtml());

  const browser = await chromium.launch({
    args: ['--use-angle=swiftshader', '--disable-gpu-sandbox'],
  });
  const report = { generatedAt: new Date().toISOString(), view: { w: VIEW_W, h: VIEW_H }, widths: WIDTHS_CSS_PX, paths: {} };

  try {
    const context = await browser.newContext({ viewport: { width: VIEW_W + 40, height: VIEW_H + 40 } });
    // 96 DPI 参照系
    const page = await context.newPage();
    page.on('pageerror', (e) => { throw new Error('pageerror: ' + e.message); });
    await page.goto('file:///' + htmlPath.replace(/\\/g, '/'));
    await page.waitForFunction(() => !!window.__probe, null, { timeout: 15000 });

    const base96 = await page.evaluate(async () => {
      const map = await window.__probe.buildMap(1);
      return await window.__probe.captureDataUrl(map);
    });
    const png96 = decodePng(base96);
    fs.writeFileSync(path.join(outDir, 'baseline-96dpi.png'), PNG.sync.write(png96));
    report.paths.baseline96 = measureRows(png96, 1);
    report.paths.baseline96Png = 'baseline-96dpi.png';

    for (const dpi of dpis) {
      const scale = dpi / 96;
      // A. 真重渲染：setPixelRatio 后重渲染
      const trueDataUrl = await page.evaluate(async (dpiVal) => {
        const map = await window.__probe.buildMap(1);
        map.setPixelRatio(dpiVal / 96);
        await map.once('idle');
        const url = await window.__probe.captureDataUrl(map);
        map.remove();
        return url;
      }, dpi);
      const pngTrue = decodePng(trueDataUrl);
      const trueName = `rerender-${dpi}dpi.png`;
      fs.writeFileSync(path.join(outDir, trueName), PNG.sync.write(pngTrue));
      // B. 放大插值：96 渲染后 drawImage 放大到同尺寸
      const upDataUrl = await page.evaluate(async ({ dataUrl, scale }) => window.__probe.upscaleDataUrl(dataUrl, scale), { dataUrl: base96, scale });
      const pngUp = decodePng(upDataUrl);
      const upName = `upscale-${dpi}dpi.png`;
      fs.writeFileSync(path.join(outDir, upName), PNG.sync.write(pngUp));
      report.paths[`${dpi}dpi`] = {
        trueRerender: measureRows(pngTrue, scale),
        trueRerenderPng: trueName,
        upscale: measureRows(pngUp, scale),
        upscalePng: upName,
      };
    }
    await context.close();
  } finally {
    await browser.close();
    fs.rmSync(htmlPath, { force: true });
  }

  const md = renderMarkdown(report);
  fs.writeFileSync(path.join(outDir, 'dpi-baseline.md'), md);
  fs.writeFileSync(path.join(outDir, 'dpi-baseline.json'), JSON.stringify(report, null, 2));
  console.log(md);
}

function measureRows(png, deviceScale) {
  const rows = WIDTHS_CSS_PX.map((_, i) => ({ y: 40 + i * 60 }));
  return measureRow(png, rows, deviceScale);
}

function renderMarkdown(report) {
  const lines = [];
  lines.push(`# DPI 线宽基线（${report.generatedAt}）`, '');
  lines.push(`视口 ${report.view.w}×${report.view.h} CSS px；「可分辨」= 对比度 ≥ ${RESOLVABLE_CONTRAST}。`, '');
  lines.push('| 名义线宽 (CSS px) | 路径 | 对比度 | 有效线宽 (CSS px) | 可分辨 |');
  lines.push('|---|---|---|---|---|');
  const b = report.paths.baseline96 || [];
  b.forEach((m, i) => lines.push(`| ${WIDTHS_CSS_PX[i]} | 96DPI 基准 | ${m.contrast} | ${m.effectiveWidthCssPx} | ${m.resolvable ? '✓' : '✗'} |`));
  for (const [dpiKey, val] of Object.entries(report.paths)) {
    if (!dpiKey.endsWith('dpi') || dpiKey === 'baseline96') continue;
    (val.trueRerender || []).forEach((m, i) => lines.push(`| ${WIDTHS_CSS_PX[i]} | ${dpiKey} 真重渲染 | ${m.contrast} | ${m.effectiveWidthCssPx} | ${m.resolvable ? '✓' : '✗'} |`));
    (val.upscale || []).forEach((m, i) => lines.push(`| ${WIDTHS_CSS_PX[i]} | ${dpiKey} 放大插值 | ${m.contrast} | ${m.effectiveWidthCssPx} | ${m.resolvable ? '✓' : '✗'} |`));
  }
  return lines.join('\n') + '\n';
}

main().catch((e) => {
  console.error('[dpi-line-probe] FAILED:', e && e.message);
  process.exit(1);
});
