/**
 * Journey 1 — 上传→分析→出图→导出 (@smoke).
 *
 * The product's main path: get data in, ask for analysis in chat, see the
 * layer mount on the map, compose the map product, export a PNG that really
 * lands on disk.
 *
 * Upload leg (#1555 closed): the dataset-upload UI entry is real now —
 * DatasetManager's attach form (source_type=upload) drives a file picker →
 * lib/api/upload.ts uploadFile (POST /api/v1/upload multipart) → attach with
 * the returned session_ref. The journey drives that real UI path with
 * setInputFiles; there is no page.evaluate API call anywhere in this file
 * (API bypass is forbidden — #1555).
 */
import { test, expect } from 'playwright/test';
import type { Page } from 'playwright/test';
import { bootstrapMock, defaultWorld, loginViaApi, sendChat, awaitShellReady, openRailTab } from '../helpers/bootstrap';
import { analysisTurn } from '../fixtures/sse';
import {
  expectMapReady,
  expectLayerListed,
  expectResultsArrived,
  expectExportLanded,
} from '../helpers/assertions';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

/**
 * 通过真实 UI 上传一个数据文件：项目面板 → 数据集 → 挂载表单（upload 档）。
 * 全程用户交互（rail tab / 按钮 / select / 文件选择器），不触碰任何 API。
 */
async function uploadViaProjectUi(page: Page): Promise<void> {
  await openRailTab(page, '项目');
  await page.getByRole('button', { name: '挂载数据集' }).first().click();
  // 来源类型 → upload 档（真实 select 交互，不用 store 注入）。
  await page.getByLabel('来源类型').selectOption('upload');
  // 真实文件选择器 → uploadFile（POST /api/v1/upload multipart）→ ref 就绪。
  await page
    .getByLabel('选择数据文件…')
    .setInputFiles({
      name: 'chengdu-schools.geojson',
      mimeType: 'application/geo+json',
      buffer: Buffer.from('{"type":"FeatureCollection","features":[]}', 'utf8'),
    });
  await expect(page.getByTestId('dataset-upload-done')).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: '确认挂载' }).click();
  await expect(page.getByText(/已挂载数据集/)).toBeVisible({ timeout: 15_000 });
  // 回对话面板：后续 chat leg 的 composer 只在对话视图存在。
  await openRailTab(page, '对话');
}

test.describe('journey-1 分析出图导出 @smoke', () => {
  test('mock：UI 上传 + 对话分析 → 图层挂载 → 制图发布 → PNG 导出落盘', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    world.pushChat(analysisTurn({ sessionId: 's-j1', taskId: 't-j1' }));
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);

    // ── 上传 leg：真实 UI（项目面板挂载表单）→ POST /upload + attach ────────
    await uploadViaProjectUi(page);
    expect(world.requests.some((r) => r.path === '/api/v1/upload' && r.method === 'POST')).toBe(true);
    expect(
      world.requests.some(
        (r) => /\/api\/v1\/projects\/[^/]+\/datasets$/.test(r.path) && r.method === 'POST',
      ),
    ).toBe(true);

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

  test('real：UI 上传 + 对话分析 → 图层挂载 → 制图发布 → PNG 导出落盘', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Real stack: the nightly lane provisions admin credentials (E2E_USER/
    // E2E_PASS) and runs a deterministic LLM stub behind LLM_BASE_URL, so the
    // turn contract holds without LLM randomness. Export requires login.
    // Upload leg drives the same real UI entry against the real
    // POST /api/v1/upload endpoint (authed via loginViaApi).
    await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);
    await uploadViaProjectUi(page);
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
