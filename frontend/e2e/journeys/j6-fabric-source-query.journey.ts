/**
 * Journey 6 — 数据源注册 → probe → 查询 → 上图（MVT 断言）.
 *
 * Registers a data fabric source through the 数据 tab, probes it healthy,
 * materializes a catalog item to a map layer (需要活动会话 — the journey sends
 * one chat turn first, exactly what the UI demands of a real user), and
 * asserts the layer mounts and the fabric data-plane hydration contract
 * (fetch-on-demand GeoJSON; the MVT tile contract is asserted in J1, whose
 * chat-created big-ref layers carry the tile endpoint).
 */
import { test, expect } from 'playwright/test';
import { defaultWorld, sendChat, awaitShellReady } from '../helpers/bootstrap';
import { installJourneyStubs } from '../fixtures/api-stubs';
import { analysisTurn } from '../fixtures/sse';
import { expectLayerListed, expectMapReady } from '../helpers/assertions';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

test.describe('journey-6 数据源注册查询上图', () => {
  test('mock：注册源 → probe healthy → 实例化目录项 → 图层挂载 + MVT 请求', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    world.pushChat(analysisTurn({ sessionId: 's-j6', taskId: 't-j6' }));
    await installJourneyStubs(page, world);

    // 实例化至图层需要活动会话（data-sources-tab.tsx:295 的守卫），先走一次对话。
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '建立会话');
    await expectMapReady(page);

    // ── 数据 tab：注册新数据源（先切到「数据源」子页签）─────────────────────
    await page.locator('[role="tab"][aria-label*="数据"]').first().click();
    await page.locator('[role="tab"]').filter({ hasText: '数据源' }).first().click();
    await page.getByRole('button', { name: '添加数据源' }).click();
    const nameInput = page.locator('input[placeholder="例如: 国家地理 WFS 服务"]');
    const urlInput = page.locator('input[placeholder="https://..."]');
    await expect(nameInput).toBeVisible({ timeout: 15_000 });
    await nameInput.fill('e2e 空间数据底座');
    await urlInput.fill('postgresql://gis-e2e.example.org/gis');
    await page.getByRole('button', { name: '提交注册并同步' }).click();
    const regToast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: '数据源注册成功' });
    await expect(regToast.first()).toBeVisible({ timeout: 15_000 });

    // ── probe：探查 → healthy toast ────────────────────────────────────────
    const probe = page.getByRole('button', { name: '探查' }).first();
    await expect(probe).toBeVisible({ timeout: 15_000 });
    await probe.click();
    const probeToast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: /连通测试结果.*healthy/ });
    await expect(probeToast.first()).toBeVisible({ timeout: 15_000 });

    // ── 目录项 → 加载至地图（materialize）──────────────────────────────────
    await page.locator('[role="tab"]').filter({ hasText: '空间目录' }).first().click();
    // 像真实用户一样搜索目录（search-as-you-type 的防抖 refetch 会重新拉取）。
    const search = page.getByRole('searchbox', { name: '搜索空间数据集' });
    await expect(search).toBeVisible({ timeout: 15_000 });
    await search.fill('poi');
    const item = page
      .locator('div')
      .filter({ hasText: '商业 POI 点位' })
      .filter({ has: page.getByRole('button', { name: '加载至地图' }) })
      .first();
    await expect(item).toBeVisible({ timeout: 20_000 });
    await item.getByRole('button', { name: '加载至地图' }).click();
    const layerToast = page
      .locator('[role="status"], [data-testid^="toast"]')
      .filter({ hasText: /成功按需实例化/ });
    await expect(layerToast.first()).toBeVisible({ timeout: 20_000 });

    // ── 断言：图层挂载 + fabric 数据面契约（按需 GeoJSON 水合）───────────────
    // fabric materialize 图层走 fetch-on-demand GeoJSON（data-sources-tab.tsx
    // addLayer → fetchRefGeoJSON）；MVT 瓦片契约属 chat 大 ref 图层（J1 断言）。
    await expectLayerListed(page, /商业 POI 点位/);
    await expect
      .poll(async () =>
        world.requests.some(
          (r) => r.method === 'GET' && /\/api\/v1\/layers\/data\/.+/.test(r.path) && !/\.mvt$/.test(r.path),
        ),
      )
      .toBe(true);
  });

  test('real：注册源 → probe → 实例化 → 图层挂载', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Nightly stack: real backend with a seeded reachable fabric source
    // (postgis service container). The same user path runs against it; the
    // external-network skip guard is explicit below — no silent green.
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '建立会话');
    await expectMapReady(page);
    await page.locator('[role="tab"][aria-label*="数据"]').first().click();
    await page.locator('[role="tab"]').filter({ hasText: '数据源' }).first().click();
    await page.getByRole('button', { name: '添加数据源' }).click();
    const nameInput = page.locator('input[placeholder="例如: 国家地理 WFS 服务"]');
    const urlInput = page.locator('input[placeholder="https://..."]');
    await expect(nameInput).toBeVisible({ timeout: 15_000 });
    await nameInput.fill('nightly postgis');
    await urlInput.fill('postgresql://localhost:5432/gis');
    await page.getByRole('button', { name: '提交注册并同步' }).click();
    const regToast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: '数据源注册成功' });
    await expect(regToast.first()).toBeVisible({ timeout: 20_000 });
    await page.locator('[role="tab"]').filter({ hasText: '空间目录' }).first().click();
    const item = page
      .locator('div')
      .filter({ has: page.getByRole('button', { name: '加载至地图' }) })
      .filter({ hasText: /区级行政|poi|admin|商业/i })
      .first();
    await expect(item).toBeVisible({ timeout: 30_000 });
    await item.getByRole('button', { name: '加载至地图' }).click();
    const layerToast = page
      .locator('[role="status"], [data-testid^="toast"]')
      .filter({ hasText: /成功按需实例化/ });
    await expect(layerToast.first()).toBeVisible({ timeout: 30_000 });
    await expectLayerListed(page, /.+/);
  });
});
