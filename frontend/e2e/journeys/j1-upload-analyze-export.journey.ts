/**
 * Journey 1 — 上传→分析→出图→导出 (@smoke).
 *
 * The product's main path: get data in, ask for analysis in chat, see the
 * layer mount on the map, compose the map product, export a PNG that really
 * lands on disk.
 *
 * KNOWN GAP (journey-discovered, tracked in the PR): the upload UI component
 * (components/upload/upload-zone.tsx) exists but is NOT mounted anywhere in
 * the current app shell — the「上传」leg has no UI entry today. The journey
 * therefore:
 *  - pins the upload API leg at the transport boundary (real POST /upload in
 *    mock mode via the same contract the unmounted component drives), and
 *  - runs the 分析→出图→导出 legs through the real UI path.
 * When the upload zone ships, the upload leg should be re-pointed at the UI.
 */
import { test, expect } from 'playwright/test';
import { bootstrapMock, defaultWorld, loginViaApi, sendChat, awaitShellReady } from '../helpers/bootstrap';
import { analysisTurn } from '../fixtures/sse';
import {
  expectMapReady,
  expectLayerListed,
  expectResultsArrived,
  expectExportLanded,
} from '../helpers/assertions';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

test.describe('journey-1 分析出图导出 @smoke', () => {
  test('mock：上传契约 + 对话分析 → 图层挂载 → 制图发布 → PNG 导出落盘', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    world.pushChat(analysisTurn({ sessionId: 's-j1', taskId: 't-j1' }));
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);

    // ── 上传 leg（transport-boundary pin — see KNOWN GAP above）────────────
    // The unmounted UploadZone drives POST /api/v1/upload with multipart form
    // data; pin that contract so the API cannot drift while the UI is absent.
    const uploadStatus = await page.evaluate(async () => {
      const form = new FormData();
      const bytes = Uint8Array.from(atob('UEsDBAoAAAAAAA=='), (c) => c.charCodeAt(0)); // zip magic prefix
      form.append('file', new Blob([bytes], { type: 'application/zip' }), 'j1.shp.zip');
      const res = await fetch('http://localhost:8001/api/v1/upload', {
        method: 'POST',
        body: form,
        headers: { 'X-Session-Token': 'e2e-anon' },
      });
      return res.status;
    });
    expect(uploadStatus).toBe(200);
    expect(world.requests.some((r) => r.path === '/api/v1/upload' && r.method === 'POST')).toBe(true);

    // ── 分析 leg：chat 指令 → SSE 回放 → 结果注册 ───────────────────────────
    await sendChat(page, '对上传的数据做热点分析');
    await expectResultsArrived(page);

    // ── 图层挂载：分析结果自动成层（大数据 ref 走 MVT 瓦片数据面）───────────
    await expectLayerListed(page, /热点|hotspot/i);
    await expectMapReady(page);
    // descriptor.feature_count(8421) > VECTOR_TILE_THRESHOLD(5000) → 生产 MVT
    // 路径启用：地图应真实请求 /layers/data/{ref}/tiles/{z}/{x}/{y}.mvt。
    await expect
      .poll(() =>
        world.requests.some((r) => /\/api\/v1\/layers\/data\/.+\.mvt$/.test(r.path)),
      )
      .toBe(true);

    // ── 出图 + 导出 leg：工作台切 compose 模式 → 制图 tab → 发布并导出 PNG ──
    // （制图 tab 仅在 compose 模式出现在 nav-rail：MODE_TABS 过滤。）
    const modeRadio = page.getByRole('radio', { name: '制图模式' });
    await expect(modeRadio).toBeVisible({ timeout: 15_000 });
    await modeRadio.click();
    await page.locator('[role="tab"][aria-label*="制图"]').first().click();
    const exportButton = page.getByRole('button', { name: /发布并导出/ }).first();
    await expect(exportButton).toBeEnabled({ timeout: 15_000 });
    await exportButton.click();

    const payload = await expectExportLanded(page);
    expect(payload.filename).toMatch(/\.png$/);
  });

  test('real：对话分析 → 图层挂载 → 制图发布 → PNG 导出落盘', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Real stack: the nightly lane provisions admin credentials (E2E_USER/
    // E2E_PASS) and runs a deterministic LLM stub behind LLM_BASE_URL, so the
    // turn contract holds without LLM randomness. Export requires login.
    await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '对上传的数据做热点分析');
    await expectResultsArrived(page);
    await expectLayerListed(page, /热点|hotspot/i);
    await expectMapReady(page);
    const modeRadio = page.getByRole('radio', { name: '制图模式' });
    await expect(modeRadio).toBeVisible({ timeout: 15_000 });
    await modeRadio.click();
    await page.locator('[role="tab"][aria-label*="制图"]').first().click();
    const exportButton = page.getByRole('button', { name: /发布并导出/ }).first();
    await expect(exportButton).toBeEnabled({ timeout: 15_000 });
    await exportButton.click();
    const payload = await expectExportLanded(page);
    expect(payload.filename).toMatch(/\.png$/);
  });
});
