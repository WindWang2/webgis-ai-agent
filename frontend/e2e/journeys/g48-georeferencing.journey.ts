/**
 * Journey G48 — Control-Point (GCP) Image Georeferencing & Deliverable GeoRaster (real mode).
 *
 * Real browser -> Next frontend -> real FastAPI backend -> GDAL/Rasterio warp & COG -> MapSpec tiles:
 *  1) Establish authenticated workspace session via real UI shell.
 *  2) Open Data Sources -> Prepare (`#rail-tab-data_sources` -> `#data-subtab-prepare`).
 *  3) Upload 128x128 RGBA PNG -> verify source metadata & pixel canvas.
 *  4) Add 5 GCPs (4 fit + 1 check) -> Save & Solve -> verify RMSE diagnostics.
 *  5) Generate Preview -> verify warped preview PNG (`naturalWidth > 0`).
 *  6) Execute GeoTIFF/COG job -> poll until artifact ready -> Bind MapSpec layer.
 *  7) Verify live XYZ raster tile PNG (HTTP 200) & authorized GeoTIFF download (HTTP 200).
 *  8) Reload browser -> verify persisted georeferencing source/plan/artifact survive refresh.
 */
import { test, expect } from 'playwright/test';
import type { Page, Response } from 'playwright/test';
import { readFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { awaitShellReady, loginViaApi, sendChat } from '../helpers/bootstrap';
import { API_ORIGIN, MODE, REAL_ONLY_REASON } from '../helpers/mode';

const STREAM_PATH = /\/api\/v1\/chat\/stream$/;

function streamResponse(page: Page, timeout = 60_000): Promise<Response> {
  return page.waitForResponse(
    (r) => STREAM_PATH.test(new URL(r.url()).pathname) && r.status() === 200,
    { timeout },
  );
}

function sessionIdOf(body: string): string | null {
  return body.match(/"session_id"\s*:\s*"([^"]+)"/)?.[1] ?? null;
}

function crc32(buf: Buffer): number {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
  }
  return (c ^ 0xffffffff) >>> 0;
}

function makeChunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

/** Synthesize a 128x128 RGBA PNG landmark image in-memory when no fixture path is supplied. */
function buildRgbaLandmarkPng(width = 128, height = 128): Buffer {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const stride = 1 + width * 4;
  const raw = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const rowOff = y * stride;
    raw[rowOff] = 0; // filter None
    for (let x = 0; x < width; x++) {
      const p = rowOff + 1 + x * 4;
      if (x >= 16 && x < 48 && y >= 16 && y < 48) {
        // Pure opaque black landmark
        raw[p] = 0;
        raw[p + 1] = 0;
        raw[p + 2] = 0;
        raw[p + 3] = 255;
      } else if (x >= 80 && x < 112 && y >= 80 && y < 112) {
        // Yellow stripe
        raw[p] = 240;
        raw[p + 1] = 230;
        raw[p + 2] = 40;
        raw[p + 3] = 255;
      } else {
        // Reddish field
        raw[p] = 225;
        raw[p + 1] = 35;
        raw[p + 2] = 35;
        raw[p + 3] = 255;
      }
    }
  }
  const idat = deflateSync(raw);
  return Buffer.concat([
    sig,
    makeChunk('IHDR', ihdr),
    makeChunk('IDAT', idat),
    makeChunk('IEND', Buffer.alloc(0)),
  ]);
}

function deg2tile(lon: number, lat: number, zoom: number): [number, number] {
  const n = 2 ** zoom;
  const x = Math.floor(((lon + 180.0) / 360.0) * n);
  const latRad = (lat * Math.PI) / 180.0;
  const y = Math.floor(
    ((1.0 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2.0) * n,
  );
  return [x, y];
}

async function openGeorefPreparePanel(page: Page): Promise<void> {
  await page.locator('#rail-tab-data_sources').click();
  const prepareTab = page.locator('#data-subtab-prepare');
  await expect(prepareTab).toBeVisible({ timeout: 15_000 });
  await prepareTab.click();
  await expect(page.getByTestId('georeferencing-workbench')).toBeVisible({ timeout: 15_000 });
}

test.describe('journey-g48 GCP Georeferencing Workbench', () => {
  test('real：上传影像 → 控制点配准求解 → 预览 → COG 执行 → MapSpec 绑定/瓦片/下载 → 刷新持久化', async ({
    page,
  }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    const token = await loginViaApi(page);
    const authHeaders = { Authorization: `Bearer ${token}` };

    await page.goto('/');
    await awaitShellReady(page);

    // 1) Create an active workspace session cleanly through chat
    const stream = streamResponse(page);
    await sendChat(page, 'g48-browser-warmup');
    const streamRes = await stream;
    const sessionId = sessionIdOf(await streamRes.text());
    expect(sessionId).toBeTruthy();

    // 2) Navigate to Data Sources -> Prepare workbench
    await openGeorefPreparePanel(page);
    const uploadInput = page.getByTestId('georef-upload-input');
    await expect(uploadInput).toBeAttached({ timeout: 15_000 });

    // 3) Upload 128x128 RGBA PNG
    const fixturePath = process.env.G48_FIXTURE_PNG;
    const pngBuf = fixturePath ? readFileSync(fixturePath) : buildRgbaLandmarkPng(128, 128);
    const uploadRespPromise = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === '/api/v1/georeferencing/sources/upload' &&
        r.request().method() === 'POST',
    );
    await uploadInput.setInputFiles({
      name: 'landmark_rgba.png',
      mimeType: 'image/png',
      buffer: pngBuf,
    });
    const uploadResp = await uploadRespPromise;
    expect(uploadResp.status()).toBe(200);

    await expect(page.getByTestId('georef-source-meta')).toContainText('128', { timeout: 15_000 });
    await expect(page.getByTestId('georef-pixel-canvas')).toBeVisible({ timeout: 15_000 });

    // 4) Enter 5 GCPs (4 fit corners + 1 interior check point) around (120.15E, 30.25N)
    const gcps: Array<{
      label: string;
      px: string;
      py: string;
      lon: string;
      lat: string;
      role: 'fit' | 'check';
    }> = [
      { label: 'P1', px: '0', py: '0', lon: '120.1495', lat: '30.2505', role: 'fit' },
      { label: 'P2', px: '128', py: '0', lon: '120.1505', lat: '30.2505', role: 'fit' },
      { label: 'P3', px: '128', py: '128', lon: '120.1505', lat: '30.2495', role: 'fit' },
      { label: 'P4', px: '0', py: '128', lon: '120.1495', lat: '30.2495', role: 'fit' },
      { label: 'CHK1', px: '64', py: '64', lon: '120.1500', lat: '30.2500', role: 'check' },
    ];
    for (let i = 0; i < gcps.length; i++) {
      await page.getByTestId('georef-add-gcp-btn').click();
      await expect(page.getByTestId(`georef-gcp-row-${i}`)).toBeVisible();
      await page.getByLabel(`label-${i}`).fill(gcps[i].label);
      await page.getByLabel(`px-${i}`).fill(gcps[i].px);
      await page.getByLabel(`py-${i}`).fill(gcps[i].py);
      await page.getByLabel(`lon-${i}`).fill(gcps[i].lon);
      await page.getByLabel(`lat-${i}`).fill(gcps[i].lat);
      if (gcps[i].role === 'check') {
        await page.getByLabel(`role-${i}`).selectOption('check');
      }
    }

    // 5) Save & Solve -> check diagnostics appear and are valid
    await page.getByTestId('georef-save-solve-btn').click();
    await expect(page.getByTestId('georef-diagnostics')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('georef-diag-status')).toContainText(/Valid/i);
    await expect(page.getByTestId('georef-diag-fit-rmse')).toContainText('m');
    await expect(page.getByTestId('georef-diag-check-rmse')).toContainText('m');

    // 6) Preview warped RGBA PNG -> verify naturalWidth > 0
    await page.getByTestId('georef-preview-btn').click();
    const previewImg = page.getByTestId('georef-preview-img');
    await expect(previewImg).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () => previewImg.evaluate((el: HTMLImageElement) => el.naturalWidth), {
        timeout: 15_000,
      })
      .toBeGreaterThan(0);

    // 7) Execute full GeoTIFF/COG job -> wait until completed status & Bind + Download appear
    await page.getByTestId('georef-execute-btn').click();
    await expect(page.getByTestId('georef-job-status')).toContainText(/GeoTIFF 已就绪|GeoTIFF Ready/i, {
      timeout: 45_000,
    });
    const bindBtn = page.getByTestId('georef-bind-layer-btn');
    await expect(bindBtn).toBeVisible({ timeout: 15_000 });

    // 8) Bind MapSpec layer -> capture real backend response & inspect live XYZ tile PNG
    const bindRespPromise = page.waitForResponse(
      (r) =>
        /\/api\/v1\/georeferencing\/plans\/.+\/bind-layer$/.test(new URL(r.url()).pathname) &&
        r.request().method() === 'POST',
    );
    await bindBtn.click();
    const bindResp = await bindRespPromise;
    expect(bindResp.status()).toBe(200);
    const bindJson = (await bindResp.json()) as {
      bound: boolean;
      layer_id: string;
      artifact?: { artifact_id: string; raster_ref: string };
      mapspec?: { layers?: Array<{ id?: string; raster_style?: { style_id?: string } }> };
    };
    expect(bindJson.bound).toBe(true);
    const rasterRef = bindJson.artifact?.raster_ref;
    const artifactId = bindJson.artifact?.artifact_id;
    const boundLayer = bindJson.mapspec?.layers?.find((ly) => ly.id === bindJson.layer_id);
    const styleId = boundLayer?.raster_style?.style_id;
    expect(rasterRef).toBeTruthy();
    expect(artifactId).toBeTruthy();
    expect(styleId).toBeTruthy();

    const [tx, ty] = deg2tile(120.15, 30.25, 16);
    const tileResp = await page.request.get(
      new URL(
        `/api/v1/raster-authoring/tiles/${encodeURIComponent(rasterRef!)}/16/${tx}/${ty}.png` +
          `?session_id=${encodeURIComponent(sessionId!)}` +
          `&layer_id=${encodeURIComponent(bindJson.layer_id)}` +
          `&style_id=${encodeURIComponent(styleId!)}`,
        API_ORIGIN,
      ).href,
      { headers: authHeaders },
    );
    expect(tileResp.status()).toBe(200);
    const tileBytes = await tileResp.body();
    expect(tileBytes.subarray(0, 4)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47]));

    // 9) Trigger authorized GeoTIFF download & verify HTTP 200 GeoTIFF payload
    const dlRespPromise = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname ===
          `/api/v1/georeferencing/artifacts/${artifactId}/download` &&
        r.request().method() === 'GET',
    );
    await page.getByTestId('georef-download-tif-btn').click();
    const dlResp = await dlRespPromise;
    expect(dlResp.status()).toBe(200);
    const dlBytes = await dlResp.body();
    expect(dlBytes.length).toBeGreaterThan(256);

    // 10) Switch back to Chat tab (app persists active tab across reload), reload & restore via History
    await page.getByRole('tab', { name: '对话' }).click();
    await page.reload();
    await awaitShellReady(page);
    await page.getByRole('button', { name: '历史记录' }).click();
    const sessionRow = page
      .getByRole('dialog', { name: '历史会话' })
      .getByRole('button', { name: /^g48-browser-warmup/ })
      .first();
    await expect(sessionRow).toBeVisible({ timeout: 15_000 });
    await sessionRow.click();
    await openGeorefPreparePanel(page);
    await expect(page.getByTestId('georef-source-meta')).toContainText('128', { timeout: 20_000 });
    await expect(page.getByTestId('georef-download-tif-btn')).toBeVisible({ timeout: 20_000 });
  });
});
