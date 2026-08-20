#!/usr/bin/env tsx
/**
 * Runtime Validator — headless Playwright driver over compiled MapSpec output.
 *
 * This is the "Seam C" node script described in the spec: it serves the
 * compiler's static `dist/` output (index.html + style.json), drives headless
 * Chromium, and verifies the documented runtime contract:
 *   - MapLibre `load` then `idle` within timeout
 *   - no console errors / page errors / failed requests / HTTP 4xx-5xx
 *   - canvas captured and not fully-transparent / near-monochrome / flat
 *   - controls within viewport and not colliding
 *   - probe DSL assertions (layer-exists, feature-count, pixel-color) when --probes given
 *
 * It prints one JSON object (the report) to stdout and exits 0 on success / 1
 * on any failure (including fatal errors, which still emit a report via
 * `fatalError`). Invoked by the Python `runtime_validator` via subprocess,
 * mirroring how `cli.ts` is invoked for compilation.
 *
 *   npx tsx runtime-validate.ts --input-dir <dist> [--out-dir <runtime>] \
 *     [--timeout 30000] [--width 1280] [--height 800] [--probes <probes.json>]
 */
import * as fs from "fs";
import * as http from "http";
import * as path from "path";
import { chromium, type Browser } from "playwright";
import { analyseCanvas, isBlank, type PixelStats } from "./canvas-analysis";
import { colorWithinTolerance, dominantColorInWindow } from "./probes";

interface Args {
  inputDir: string;
  outDir?: string;
  timeout: number;
  width: number;
  height: number;
  probesPath?: string;
}

function parseArgs(): Args {
  const argv = process.argv.slice(2);
  let inputDir: string | undefined;
  let outDir: string | undefined;
  let probesPath: string | undefined;
  let timeout = 30000;
  let width = 1280;
  let height = 800;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = argv[i + 1];
    if (a === "--input-dir" || a === "-i") { inputDir = next; i++; }
    else if (a === "--out-dir" || a === "-o") { outDir = next; i++; }
    else if (a === "--probes" || a === "-p") { probesPath = next; i++; }
    else if (a === "--timeout") { timeout = parseInt(next, 10) || timeout; i++; }
    else if (a === "--width") { width = parseInt(next, 10) || width; i++; }
    else if (a === "--height") { height = parseInt(next, 10) || height; i++; }
  }
  if (!inputDir) {
    console.error("Usage: npx tsx runtime-validate.ts --input-dir <dist> [--out-dir <runtime>]");
    process.exit(2);
  }
  return { inputDir, outDir, timeout, width, height, probesPath };
}

interface ControlBox {
  selector: string;
  top: number; left: number; width: number; height: number;
}

interface ProbeResult {
  probe: Record<string, unknown>;
  pass: boolean;
  expected: unknown;
  actual: unknown;
}

interface RuntimeReport {
  mapLoaded: boolean;
  mapIdle: boolean;
  consoleErrors: string[];
  pageErrors: string[];
  failedRequests: { url: string; status: number | null; method: string }[];
  canvas: (PixelStats & { captured: boolean; blank: boolean; blankReason: string }) | null;
  controls: { overflow: string[]; collisions: string[] };
  fatalError: string | null;
  probeResults?: ProbeResult[];
}

function emptyReport(fatalError: string | null): RuntimeReport {
  return {
    mapLoaded: false,
    mapIdle: false,
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
    canvas: null,
    controls: { overflow: [], collisions: [] },
    fatalError,
  };
}

/** Start a read-only static file server over the compiled dir. */
function startStaticServer(root: string): { server: http.Server; port: number; stop: () => Promise<void> } {
  const mime: Record<string, string> = {
    ".html": "text/html",
    ".json": "application/json",
    ".js": "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
  };
  const server = http.createServer((req, res) => {
    // Only GET; deny anything that isn't a plain file under root (no traversal).
    const urlPath = decodeURIComponent((req.url ?? "/").split("?")[0]);
    if (req.method !== "GET" || urlPath.includes("..")) {
      res.writeHead(403);
      res.end("forbidden");
      return;
    }
    let filePath = path.join(root, urlPath);
    if (urlPath.endsWith("/")) filePath = path.join(filePath, "index.html");
    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404);
        res.end("not found");
        return;
      }
      res.writeHead(200, { "Content-Type": mime[path.extname(filePath)] ?? "application/octet-stream" });
      res.end(data);
    });
  });
  return {
    server,
    port: 0, // set after listen
    stop: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

function boxesOverlap(a: ControlBox, b: ControlBox): boolean {
  // inclusive-axis overlap test
  return (
    a.left < b.left + b.width &&
    a.left + a.width > b.left &&
    a.top < b.top + b.height &&
    a.top + a.height > b.top
  );
}

async function run(): Promise<RuntimeReport> {
  const args = parseArgs();
  const indexHtml = path.join(args.inputDir, "index.html");
  if (!fs.existsSync(indexHtml)) {
    return emptyReport(`missing compiled index.html at ${indexHtml}`);
  }

  // Load probes if requested.
  let probesSpec: { expect?: string; probes: Record<string, unknown>[] } | null = null;
  if (args.probesPath) {
    try {
      const raw = fs.readFileSync(args.probesPath, "utf-8");
      probesSpec = JSON.parse(raw);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      const r = emptyReport(`failed to load probes: ${msg}`);
      (r as RuntimeReport).probeResults = [];
      return r;
    }
  }

  const { server, stop } = startStaticServer(args.inputDir);
  const port: number = await new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve(addr && typeof addr === "object" ? addr.port : 0);
    });
  });

  const report = emptyReport(null);
  let browser: Browser | null = null;
  // Keep screenshot PNG for pixel-color probes (reuse same buffer).
  let capturedPng: { width: number; height: number; data: Uint8Array | Buffer } | null = null;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
    });
    // Start tracing up front so a later stop always has something to flush.
    // snapshots=true keeps memory bounded; screenshots are taken on stop.
    await context.tracing.start({ screenshots: true, snapshots: true });
    const page = await context.newPage();

    page.on("console", (msg) => {
      if (msg.type() === "error") report.consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => {
      report.pageErrors.push(err.message);
    });
    page.on("response", (res) => {
      const status = res.status();
      if (status >= 400) {
        report.failedRequests.push({ url: res.url(), status, method: res.request().method() });
      }
    });

    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "domcontentloaded", timeout: args.timeout });

    // Wait for MapLibre load, then idle. The HTML template sets these globals.
    await page.waitForFunction(() => (window as unknown as Record<string, unknown>).__MAP_LOADED__ === true, { timeout: args.timeout });
    report.mapLoaded = true;
    await page.waitForFunction(() => (window as unknown as Record<string, unknown>).__MAP_IDLE__ === true, { timeout: args.timeout });
    report.mapIdle = true;

    // Capture the map canvas and analyse it.
    const canvas = await page.$("canvas");
    if (canvas) {
      const pngBuffer = await canvas.screenshot();
      const { PNG } = await import("pngjs");
      const png = PNG.sync.read(pngBuffer as Buffer);
      capturedPng = png;
      const stats = analyseCanvas(png.data as Uint8Array, png.width, png.height);
      const verdict = isBlank(stats);
      report.canvas = {
        ...stats,
        captured: true,
        blank: verdict.blank,
        blankReason: verdict.reason,
      };
      // Persist the screenshot if an out dir was given.
      if (args.outDir) {
        fs.mkdirSync(args.outDir, { recursive: true });
        fs.writeFileSync(path.join(args.outDir, "map.png"), pngBuffer);
      }
    } else {
      report.pageErrors.push("no <canvas> element found on the page");
    }

    // Execute probes AFTER __MAP_IDLE__, reusing captured PNG for pixel-color.
    if (probesSpec && probesSpec.probes) {
      const probeResults: ProbeResult[] = [];
      for (const probe of probesSpec.probes) {
        const pType = probe.type as string;
        if (pType === "layer-exists") {
          const layer = probe.layer as string;
          const exists = await page.evaluate((lid: string) => {
            const m = (window as unknown as Record<string, unknown>).__MAP__ as { getLayer?: (id: string) => unknown } | undefined;
            if (!m || typeof m.getLayer !== "function") return false;
            return m.getLayer(lid) != null;
          }, layer);
          probeResults.push({
            probe: probe as Record<string, unknown>,
            pass: exists === true,
            expected: `layer "${layer}" exists`,
            actual: exists ? `layer "${layer}" exists` : `layer "${layer}" not found`,
          });
        } else if (pType === "feature-count") {
          const layer = probe.layer as string;
          const equalsVal = probe.equals as number | undefined;
          const minVal = probe.min as number | undefined;
          const filterVal = probe.filter as unknown;
          const count: number = await page.evaluate(
            ({ lid, flt }: { lid: string; flt: unknown }) => {
              const m = (window as unknown as Record<string, unknown>).__MAP__ as
                | { queryRenderedFeatures?: (geo?: unknown, opts?: unknown) => unknown[] }
                | undefined;
              if (!m || typeof m.queryRenderedFeatures !== "function") return 0;
              const opts: Record<string, unknown> = { layers: [lid] };
              if (flt !== undefined) opts.filter = flt;
              try {
                const feats = m.queryRenderedFeatures(undefined, opts);
                return Array.isArray(feats) ? feats.length : 0;
              } catch {
                return 0;
              }
            },
            { lid: layer, flt: filterVal }
          );
          let pass = true;
          let expectedDesc = "";
          if (equalsVal !== undefined && minVal !== undefined) {
            pass = count === equalsVal && count >= minVal;
            expectedDesc = `count == ${equalsVal} && >= ${minVal}`;
          } else if (equalsVal !== undefined) {
            pass = count === equalsVal;
            expectedDesc = `count == ${equalsVal}`;
          } else if (minVal !== undefined) {
            pass = count >= minVal;
            expectedDesc = `count >= ${minVal}`;
          } else {
            // no equals/min → just report count, fail open (shouldn't happen if validated)
            expectedDesc = `count exists`;
            pass = true;
          }
          probeResults.push({
            probe: probe as Record<string, unknown>,
            pass,
            expected: expectedDesc,
            actual: count,
          });
        } else if (pType === "pixel-color") {
          // probe.layer is contract-required for reporting context; sampling uses `at`.
          const at = probe.at as [number, number];
          const expectHex = probe.expect as string;
          if (!capturedPng) {
            probeResults.push({
              probe: probe as Record<string, unknown>,
              pass: false,
              expected: expectHex,
              actual: "no canvas screenshot available",
            });
            continue;
          }
          // Project lng/lat → screen coords in page.
          const screen = await page.evaluate((coords: [number, number]) => {
            const m = (window as unknown as Record<string, unknown>).__MAP__ as
              | { project?: (lngLat: [number, number]) => { x: number; y: number } }
              | undefined;
            if (!m || typeof m.project !== "function") return null;
            try {
              const pt = m.project(coords);
              return { x: Math.round(pt.x), y: Math.round(pt.y) };
            } catch {
              return null;
            }
          }, at);
          if (!screen) {
            probeResults.push({
              probe: probe as Record<string, unknown>,
              pass: false,
              expected: expectHex,
              actual: "project failed (map not available)",
            });
            continue;
          }
          const { x, y } = screen as { x: number; y: number };
          const w = capturedPng.width;
          const h = capturedPng.height;
          if (x < 0 || x >= w || y < 0 || y >= h) {
            probeResults.push({
              probe: probe as Record<string, unknown>,
              pass: false,
              expected: expectHex,
              actual: `point [${at[0]},${at[1]}] projects outside canvas (${x},${y}) vs ${w}x${h}`,
            });
            continue;
          }
          const dominant = dominantColorInWindow(capturedPng, x, y, 2);
          if (!dominant) {
            probeResults.push({
              probe: probe as Record<string, unknown>,
              pass: false,
              expected: expectHex,
              actual: `no opaque pixels near (${x},${y})`,
            });
            continue;
          }
          const pass = colorWithinTolerance(dominant, expectHex, 16);
          const actualHex = `#${dominant.r.toString(16).padStart(2, "0")}${dominant.g.toString(16).padStart(2, "0")}${dominant.b.toString(16).padStart(2, "0")}`;
          probeResults.push({
            probe: probe as Record<string, unknown>,
            pass,
            expected: expectHex,
            actual: actualHex,
          });
        } else {
          probeResults.push({
            probe: probe as Record<string, unknown>,
            pass: false,
            expected: "known probe type",
            actual: `unknown probe type "${pType}"`,
          });
        }
      }
      report.probeResults = probeResults;
    }

    // Control overflow + collision checks over every MapLibre control container.
    const controlBoxes: ControlBox[] = await page.$$eval(
      ".maplibregl-ctrl, [data-webgis-control]",
      (els) =>
        els.map((el) => {
          const r = el.getBoundingClientRect();
          return {
            selector: el.className || el.getAttribute("data-webgis-control") || "ctrl",
            top: r.top,
            left: r.left,
            width: r.width,
            height: r.height,
          };
        })
    );
    const vw = args.width;
    const vh = args.height;
    for (const c of controlBoxes) {
      if (c.left < 0 || c.top < 0 || c.left + c.width > vw || c.top + c.height > vh) {
        report.controls.overflow.push(`${c.selector} outside viewport`);
      }
    }
    for (let i = 0; i < controlBoxes.length; i++) {
      for (let j = i + 1; j < controlBoxes.length; j++) {
        if (boxesOverlap(controlBoxes[i], controlBoxes[j])) {
          report.controls.collisions.push(`${controlBoxes[i].selector} ↔ ${controlBoxes[j].selector}`);
        }
      }
    }

    // Save a Playwright trace if requested, for post-hoc replay.
    if (args.outDir) {
      await context.tracing.stop({ path: path.join(args.outDir, "trace.zip") });
    } else {
      await context.tracing.stop();
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    report.fatalError = msg;
  } finally {
    if (browser) await browser.close();
    await stop();
  }
  return report;
}

run()
  .then((report) => {
    // Persist probe-aware report.json to outDir if provided (so Python wrapper sees probeResults even before its own write).
    const args = parseArgs();
    if (args.outDir) {
      try {
        fs.mkdirSync(args.outDir, { recursive: true });
        fs.writeFileSync(path.join(args.outDir, "report.json"), JSON.stringify(report, null, 2), "utf-8");
      } catch {
        // ignore
      }
    }
    // Always emit the full report to stdout; exit code signals pass/fail.
    process.stdout.write(JSON.stringify(report, null, 2));
    const probeFailed = (report.probeResults ?? []).some((r) => !r.pass);
    const failed =
      report.fatalError !== null ||
      !report.mapLoaded ||
      !report.mapIdle ||
      report.consoleErrors.length > 0 ||
      report.pageErrors.length > 0 ||
      report.failedRequests.length > 0 ||
      (report.canvas?.blank ?? true) ||
      probeFailed;
    process.exit(failed ? 1 : 0);
  })
  .catch((err) => {
    process.stdout.write(JSON.stringify(emptyReport(`uncaught: ${(err as Error)?.message ?? err}`), null, 2));
    process.exit(1);
  });
