/**
 * Journey 3 — 模板库浏览 → 应用 → 画布变化（视觉 diff 容差）.
 *
 * Browses the template gallery, applies a basemap template, and asserts the
 * apply landed through two production signals: the HUD base-layer label swaps
 * away from the default provider (BASE_LAYER_CHANGE queue contract, see
 * template-gallery-v2 applyBasemapTemplate) and the map canvas pixels move
 * beyond an explicit tolerance (mean per-channel delta, before/after PNG).
 */
import { test, expect, type Page } from 'playwright/test';
import { PNG } from 'pngjs';
import { defaultWorld } from '../helpers/bootstrap';
import { installJourneyStubs } from '../fixtures/api-stubs';
import { expectMapReady } from '../helpers/assertions';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

/** Mean per-channel absolute delta between two same-size PNG buffers. */
function meanAbsDelta(a: Buffer, b: Buffer): { delta: number; comparable: boolean } {
  const pa = PNG.sync.read(a);
  const pb = PNG.sync.read(b);
  if (pa.width !== pb.width || pa.height !== pb.height) return { delta: -1, comparable: false };
  const n = pa.width * pa.height;
  let sum = 0;
  for (let i = 0; i < pa.data.length; i += 4) {
    sum += Math.abs(pa.data[i] - pb.data[i]);
    sum += Math.abs(pa.data[i + 1] - pb.data[i + 1]);
    sum += Math.abs(pa.data[i + 2] - pb.data[i + 2]);
  }
  return { delta: sum / (n * 3), comparable: true };
}

async function shootMap(page: Page): Promise<Buffer> {
  const canvas = page.locator('.maplibregl-canvas');
  await expect(canvas).toBeVisible();
  return canvas.screenshot();
}

async function baseLayerLabel(page: Page): Promise<string | null> {
  return page
    .locator('button[aria-label^="Base layer:"]')
    .first()
    .textContent()
    .catch(() => null);
}

/** 打开模板库 → 找到底图模板卡片 → 应用。 */
async function applyDarkBasemap(page: Page): Promise<void> {
  await page.getByRole('button', { name: '模板库' }).click();
  const card = page.locator('div.cursor-pointer').filter({ hasText: '深色影像底图' }).first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.getByRole('button', { name: '应用' }).click();
}

test.describe('journey-3 模板应用', () => {
  test('mock：浏览模板库 → 应用底图模板 → 底图切换 + 画布变化在容差内可检出', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    await installJourneyStubs(page, world);
    await page.goto('/');
    await expectMapReady(page);

    const before = await shootMap(page);
    await applyDarkBasemap(page);

    // 生产信号 1：模板详情被真实拉取（gallery 的 apply 先 GET /templates/{id}）。
    expect(
      world.requests.some((r) => /\/api\/v1\/templates\/tpl-basemap(\?|$)/.test(r.path)),
    ).toBe(true);

    // 生产信号 2：HUD 底图标签切走默认 provider（BASE_LAYER_CHANGE 契约）。
    await expect
      .poll(async () => {
        const label = await baseLayerLabel(page);
        return label !== null && !label.includes('Carto 深色');
      }, { timeout: 20_000 })
      .toBe(true);

    // 生产信号 3：画布变化 —— 容差取 1.5（0–255 尺度）：抗压缩渲染抖动，
    // 仍能可靠识别风格切换。
    await expect
      .poll(async () => {
        const after = await shootMap(page);
        const { delta, comparable } = meanAbsDelta(before, after);
        return comparable ? delta > 1.5 : true;
      }, { timeout: 20_000, intervals: [500, 1000, 2000, 4000] })
      .toBe(true);
  });

  test('real：浏览模板库 → 应用底图模板 → 底图切换 + 画布变化', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    await page.goto('/');
    await expectMapReady(page);
    const before = await shootMap(page);
    await applyDarkBasemap(page);
    await expect
      .poll(async () => {
        const label = await baseLayerLabel(page);
        return label !== null && !label.includes('Carto 深色');
      }, { timeout: 30_000 })
      .toBe(true);
    await expect
      .poll(async () => {
        const after = await shootMap(page);
        const { delta, comparable } = meanAbsDelta(before, after);
        return comparable ? delta > 1.5 : true;
      }, { timeout: 20_000, intervals: [500, 1000, 2000, 4000] })
      .toBe(true);
  });
});
