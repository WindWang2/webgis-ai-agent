// PROTOTYPE (throwaway) — wayfinder ticket-007 页面入口（由 esbuild 打包，依赖解析归 bundler）
import maplibregl from "maplibre-gl";
import * as GeoTIFF from "geotiff";

const $ = (id) => document.getElementById(id);
const log = (m) => { $("log").textContent += `[${new Date().toISOString().slice(11, 23)}] ${m}\n`; };
const fmtB = (n) => (n > 1048576 ? (n / 1048576).toFixed(2) + " MiB" : (n / 1024).toFixed(1) + " KiB");
const fmtMs = (n) => (n > 1000 ? (n / 1000).toFixed(2) + " s" : n.toFixed(0) + " ms");
window.__poc = { steps: {} }; // 供无头检查读取

const map = new maplibregl.Map({
  container: "map",
  center: [116.4, 39.9], zoom: 11,
  style: { version: 8, sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OSM" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] },
});

const worker = new Worker("./dist/worker.js", { type: "module" });
let rpcId = 0; const pending = new Map();
worker.onmessage = (e) => { const p = pending.get(e.data.id); pending.delete(e.data.id); e.data.ok ? p.resolve(e.data.result) : p.reject(new Error(e.data.error)); };
const rpc = (op, payload) => new Promise((resolve, reject) => { const id = ++rpcId; pending.set(id, { resolve, reject }); worker.postMessage({ id, op, payload }); });

const summary = () => {
  const s = window.__poc.steps;
  const lines = [];
  if (s.init) lines.push(`wasm: ${s.init.wasmResources.map((r) => `${r.name} 线传 ${fmtB(r.transferSize)} / 解压 ${fmtB(r.decodedBodySize)}`).join("；")} · 编译 ${fmtMs(s.init.initMs)} · 工具数 ${s.init.toolCount}`);
  if (s.dissolve) lines.push(`dissolve: 输入 ${s.dissolve.featuresIn} 面/${fmtB(s.dissolve.inBytes)} → 输出 ${s.dissolve.featuresOut} 面/${fmtB(s.dissolve.outBytes)} · ${fmtMs(s.dissolve.runMs)} · 回传体积≈${fmtB(s.dissolve.outBytes)}`);
  if (s.viewshed) lines.push(`viewshed: DEM ${fmtB(s.viewshed.demBytes)} → COG ${fmtB(s.viewshed.outBytes)} · ${fmtMs(s.viewshed.runMs)} · 回传体积≈${fmtB(s.viewshed.outBytes)}`);
  $("summary").textContent = lines.join("\n") || "（未初始化）";
};

$("btn-init").onclick = async () => {
  $("btn-init").disabled = true; $("btn-init").textContent = "① 初始化中…";
  try {
    const r = await rpc("init");
    window.__poc.steps.init = r;
    log(`工具箱就绪：${r.toolCount} 个工具，编译 ${fmtMs(r.initMs)}`);
    for (const w of r.wasmResources) log(`  ${w.name}: 线传 ${fmtB(w.transferSize)}（gzip）/ 解压 ${fmtB(w.decodedBodySize)}`);
    ["btn-dissolve", "btn-viewshed", "btn-manifests"].forEach((id) => { $(id).disabled = false; });
  } catch (e) { log(`✗ init 失败: ${e.message}`); $("btn-init").disabled = false; }
  summary();
};

$("btn-manifests").onclick = async () => {
  const r = await rpc("manifests", { tools: ["dissolve", "viewshed"] });
  window.__poc.steps.manifests = r;
  log(`manifests（共 ${r.total} 个，示 ${r.manifests.length} 个）：`);
  log(JSON.stringify(r.manifests, null, 1).slice(0, 2600));
};

$("btn-dissolve").onclick = async () => {
  $("btn-dissolve").disabled = true;
  try {
    const polys = await (await fetch("./sample-polys.geojson")).json();
    map.addSource("polys-in", { type: "geojson", data: polys });
    map.addLayer({ id: "polys-in", type: "fill", source: "polys-in", paint: { "fill-color": "#3b82f6", "fill-opacity": 0.25 } });
    log(`输入 ${polys.features.length} 个多边形已上图`);
    const bytes = new TextEncoder().encode(JSON.stringify(polys));
    const args = $("args-dissolve").value.trim().split(/\s+/);
    const r = await rpc("run", { tool: "dissolve", args, input: { "polys.geojson": bytes.buffer } });
    if (r.exitCode !== 0) throw new Error("exit " + r.exitCode + " :: " + r.stdout.join(" | "));
    const out = JSON.parse(new TextDecoder().decode(r.files["dissolved.geojson"]));
    map.addSource("polys-out", { type: "geojson", data: out });
    map.addLayer({ id: "polys-out", type: "line", source: "polys-out", paint: { "line-color": "#dc2626", "line-width": 2 } });
    const bbox = new maplibregl.LngLatBounds();
    for (const f of polys.features) for (const c of f.geometry.coordinates[0]) bbox.extend(c);
    map.fitBounds(bbox, { padding: 60 });
    window.__poc.steps.dissolve = { ...r, featuresIn: polys.features.length, featuresOut: out.features?.length ?? "?" };
    log(`✓ dissolve：${fmtB(r.inBytes)} 进 → ${fmtB(r.outBytes)} 出（${out.features?.length} 面），耗时 ${fmtMs(r.runMs)}`);
  } catch (e) { log(`✗ dissolve 失败: ${e.message}`); }
  $("btn-dissolve").disabled = false; summary();
};

let viewshedState = null;
$("btn-viewshed").onclick = async () => {
  $("btn-viewshed").disabled = true;
  try {
    const meta = await (await fetch("./sample-meta.json")).json();
    const demBuf = await (await fetch("./sample-dem.tif")).arrayBuffer();
    // 取 DEM 中心像素值作观测站高程
    const tif = await GeoTIFF.fromArrayBuffer(demBuf);
    const img = await tif.getImage();
    const rasters = await img.readRasters();
    const w = img.getWidth(), h = img.getHeight();
    const z = rasters[0][Math.floor(h / 2) * w + Math.floor(w / 2)];
    log(`DEM ${w}×${h}，中心高程 ${z.toFixed(1)} m，观测站 +15 m`);
    // manifest 权威定义：stations 是矢量输入文件（非内联坐标）——生成观测点 GeoJSON
    const stations = {
      type: "FeatureCollection",
      features: [{
        type: "Feature", properties: { z: +(z + 15).toFixed(1) },
        geometry: { type: "Point", coordinates: [meta.centerUTM.e, meta.centerUTM.n] },
      }],
    };
    const stationsBytes = new TextEncoder().encode(JSON.stringify(stations));
    let args = $("args-viewshed").value.trim().split(/\s+/);
    if (!args.some((a) => a.startsWith("--stations")))
      args.push("--stations=/work/stations.geojson");
    if (!args.some((a) => a.startsWith("--height")))
      args.push(`--height=15`);
    $("args-viewshed").value = args.join(" ");
    const r = await rpc("run", { tool: "viewshed", args, input: { "dem.tif": demBuf, "stations.geojson": stationsBytes.buffer } });
    if (r.exitCode !== 0) throw new Error("exit " + r.exitCode + " :: " + r.stdout.join(" | "));
    const key = Object.keys(r.files).find((k) => k.endsWith(".tif"));
    const outTif = await GeoTIFF.fromArrayBuffer(r.files[key]);
    const outImg = await outTif.getImage();
    const outRasters = await outImg.readRasters();
    viewshedState = { data: outRasters[0], w: outImg.getWidth(), h: outImg.getHeight(), bounds: meta.bounds4326 };
    drawOverlay();
    map.fitBounds([[meta.bounds4326.west, meta.bounds4326.south], [meta.bounds4326.east, meta.bounds4326.north]], { padding: 60 });
    const visible = viewshedState.data.reduce((a, v) => a + (v > 0 ? 1 : 0), 0);
    window.__poc.steps.viewshed = { ...r, demBytes: demBuf.byteLength, stationZ: z + 15, visiblePx: visible, totalPx: viewshedState.w * viewshedState.h };
    log(`✓ viewshed：DEM ${fmtB(demBuf.byteLength)} 进 → COG ${fmtB(r.outBytes)} 出，耗时 ${fmtMs(r.runMs)}，可见像元 ${visible}/${viewshedState.w * viewshedState.h}`);
  } catch (e) { log(`✗ viewshed 失败: ${e.message}`); }
  $("btn-viewshed").disabled = false; summary();
};

function drawOverlay() {
  if (!viewshedState) return;
  const cv = $("overlay"), mapCv = map.getCanvas();
  cv.width = mapCv.width; cv.height = mapCv.height;
  const ctx = cv.getContext("2d");
  const { data, w, h, bounds } = viewshedState;
  const nw = map.project([bounds.west, bounds.north]), se = map.project([bounds.east, bounds.south]);
  const tmp = document.createElement("canvas"); tmp.width = w; tmp.height = h;
  const tctx = tmp.getContext("2d");
  const img = tctx.createImageData(w, h);
  let painted = 0;
  for (let i = 0; i < data.length; i++) {
    if (data[i] > 0) { img.data[i * 4] = 22; img.data[i * 4 + 1] = 163; img.data[i * 4 + 2] = 74; img.data[i * 4 + 3] = 150; painted++; }
  }
  tctx.putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.drawImage(tmp, nw.x, nw.y, se.x - nw.x, se.y - nw.y);
  window.__poc.overlay = { canvas: [cv.width, cv.height], srcPainted: painted, srcTotal: data.length, rect: [Math.round(nw.x), Math.round(nw.y), Math.round(se.x), Math.round(se.y)] };
}
map.on("moveend", drawOverlay);
