/**
 * Journey 6 — 数据源准入 → probe → 查询 → 上图.
 *
 * Mock covers public source registration. Real verifies private registration
 * is denied, then consumes a runner-configured source. Both probe it healthy,
 * materializes a catalog item to a map layer (需要活动会话 — the journey sends
 * one chat turn first, exactly what the UI demands of a real user), and
 * asserts the layer mounts and the fabric data-plane hydration contract
 * (fetch-on-demand GeoJSON; the MVT tile contract is asserted in J1, whose
 * chat-created big-ref layers carry the tile endpoint).
 */
import { test, expect } from 'playwright/test';
import { defaultWorld, sendChat, awaitShellReady, loginViaApi } from '../helpers/bootstrap';
import { installJourneyStubs } from '../fixtures/api-stubs';
import { analysisTurn } from '../fixtures/sse';
import { expectLayerListed, expectMapReady } from '../helpers/assertions';
import { API_ORIGIN, MODE, REAL_ONLY_REASON } from '../helpers/mode';

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

  test('real：拒绝私网注册 → 已配置源 probe → 实例化 → 图层挂载', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Public registration cannot grant private-network access. The runner
    // configures its owned PostGIS fixture through the trusted manager path;
    // this journey proves both the rejection and real data consumption.
    const token = await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '建立会话');
    await expect(page.getByText('会话已建立，可以开始数据查询。', { exact: true })).toBeVisible();
    await expectMapReady(page);
    await page.locator('[role="tab"][aria-label*="数据"]').first().click();
    await page.locator('[role="tab"]').filter({ hasText: '数据源' }).first().click();
    await page.getByRole('button', { name: '添加数据源' }).click();
    await page.getByLabel('数据源名称', { exact: true }).fill('blocked private source');
    await page.getByLabel('协议类型', { exact: true }).selectOption('postgis');
    await page.locator('input[placeholder="https://..."]').fill('postgresql://localhost:5432/gis');
    // Even this legacy public checkbox cannot grant server-side privileges.
    await page.getByLabel('允许内网 (SSRF)', { exact: true }).check();
    const rejected = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/v1/data-fabric/sources'
      && response.request().method() === 'POST');
    await page.getByRole('button', { name: '提交注册并同步' }).click();
    expect((await rejected).status()).toBe(400);
    const sourcesResponse = await page.request.get(new URL('/api/v1/data-fabric/sources', API_ORIGIN).href, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(sourcesResponse.ok()).toBe(true);
    const sources = (await sourcesResponse.json()).sources as Array<{ name: string }>;
    expect(sources.some((source) => source.name === 'blocked private source')).toBe(false);
    expect(sources.some((source) => source.name === 'nightly postgis fixture')).toBe(true);

    const source = page.locator('div').filter({ hasText: 'nightly postgis fixture' })
      .filter({ has: page.getByRole('button', { name: '探查', exact: true }) }).last();
    await source.getByRole('button', { name: '探查', exact: true }).click();
    await expect(page.locator('[role="status"], [data-testid^="toast"]')
      .filter({ hasText: /连通测试结果.*healthy/ }).first()).toBeVisible({ timeout: 20_000 });
    await page.locator('[role="tab"]').filter({ hasText: '空间目录' }).first().click();
    await page.getByRole('searchbox', { name: '搜索空间数据集' }).fill('poi_commercial');
    const item = page.locator('div').filter({ hasText: /poi_commercial/ })
      .filter({ has: page.getByRole('button', { name: '加载至地图', exact: true }) }).last();
    await expect(item).toBeVisible({ timeout: 30_000 });
    const materialized = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/v1/data-fabric/materialize'
      && response.request().method() === 'POST');
    await item.getByRole('button', { name: '加载至地图', exact: true }).click();
    const response = await materialized;
    expect(response.ok()).toBe(true);
    const result = await response.json();
    expect(result.feature_count).toBeGreaterThan(0);
    expect(result.ref_id).toBeTruthy();
    await expect(page.locator('[role="status"], [data-testid^="toast"]')
      .filter({ hasText: /成功按需实例化/ }).first()).toBeVisible({ timeout: 30_000 });
    await expectLayerListed(page, /poi_commercial/);
  });
});
