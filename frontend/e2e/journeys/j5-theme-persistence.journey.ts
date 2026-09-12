/**
 * Journey 5 — 暗色切换持久化：切主题 + accent → 刷新 → 不闪白 (@smoke).
 *
 * The persisted theme must be applied BEFORE first paint by the inline
 * bootstrap script in app/layout.tsx (reading the same `geoagent-settings`
 * localStorage key zustand persists to). The pre-paint probe records the
 * documentElement state at init-script time — before any paint can happen —
 * so asserting on it is exactly the「不闪白」contract, not a hydration-time
 * afterthought.
 */
import { test, expect } from 'playwright/test';
import { awaitShellReady, installThemeProbe } from '../helpers/bootstrap';
import { defaultWorld } from '../helpers/bootstrap';
import { installJourneyStubs } from '../fixtures/api-stubs';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

/** Flip theme through the settings panel UI (production path): top-bar
 * "UI 调整" opener → 主题 radiogroup → 暗色 radio (tweaks-panel SegmentedControl). */
async function applyDarkThemeViaUi(page: import('playwright/test').Page): Promise<void> {
  await page.locator('button[aria-label="UI 调整"]').first().click();
  const darkRadio = page
    .locator('[role="radiogroup"][aria-label="主题"] button[role="radio"]')
    .filter({ hasText: '暗色' });
  await expect(darkRadio).toBeVisible({ timeout: 15_000 });
  await darkRadio.click();
  // accent 颜色：面板若提供 color input 则一并设置（持久化断言覆盖 accent 值）。
  const accent = page.locator('input[type="color"]').first();
  if (await accent.count()) {
    await accent.fill('#ff5533');
  }
  await page.locator('[role="dialog"]').getByRole('button', { name: '关闭' }).click();
}

test.describe('journey-5 主题持久化 @smoke', () => {
  test('mock：暗色 + accent 切换 → 刷新 → pre-paint 即为暗色（不闪白）', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    await installJourneyStubs(page, world);
    await page.goto('/');
    await awaitShellReady(page);

    await applyDarkThemeViaUi(page);

    // 切换后：hydration 层面已生效（documentElement 暗色）。
    await expect
      .poll(async () => page.evaluate(() => document.documentElement.classList.contains('dark')))
      .toBe(true);
    // zustand persist 已写入（settings 键）。
    const persisted = await page.evaluate(() => window.localStorage.getItem('geoagent-settings') ?? '');
    expect(persisted).toContain('"theme":"dark"');

    // 刷新：pre-paint probe（init script 先于一切应用脚本与首帧运行）。
    await installThemeProbe(page);
    await page.reload();
    await awaitShellReady(page);
    const atStart = await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__e2eThemeAtStart,
    ) as { dark: boolean; theme: string | null; accent: string };
    expect(atStart.dark, 'first observable paint state must already be dark (no white flash)').toBe(true);
    expect(atStart.theme).toBe('dark');
  });

  test('real：暗色切换 → 刷新 → pre-paint 即为暗色', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    await page.goto('/');
    await awaitShellReady(page);
    await applyDarkThemeViaUi(page);
    await expect
      .poll(async () => page.evaluate(() => document.documentElement.classList.contains('dark')))
      .toBe(true);
    await installThemeProbe(page);
    await page.reload();
    await awaitShellReady(page);
    const atStart = await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__e2eThemeAtStart,
    ) as { dark: boolean; theme: string | null };
    expect(atStart.dark, 'first observable paint state must already be dark (no white flash)').toBe(true);
  });
});
