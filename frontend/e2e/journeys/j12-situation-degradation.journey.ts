/**
 * Journey 12 — 情境面诚实降级（ADR-0283 D5）.
 *
 * situation 端点不可达（真实 500）→ 状态条诚实「不可用」而非伪装空态；
 * 无 scope 会话 → 中性 chip（不编造范围）；应用壳不崩溃。
 */
import { test, expect } from 'playwright/test';
import { bootstrapMock, defaultWorld, awaitShellReady, sendChat } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

test.describe('journey-12 情境面降级', () => {
  test.skip(MODE !== 'mock', REAL_ONLY_REASON);
  test('mock：situation 不可达 → 诚实不可用，壳不崩溃', async ({ page }) => {
    const world = defaultWorld();
    await bootstrapMock(page, world);
    // 覆盖 situation 路由为真实 500（最后注册优先匹配）。
    await page.route(/\/api\/v1\/chat\/sessions\/[^/]+\/situation(\?|$)/, async (route) => {
      await route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
    });
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '分析学校分布');
    await page.waitForTimeout(1500);

    const bar = page.getByTestId('situation-status-bar');
    await expect(bar).toBeVisible({ timeout: 15_000 });
    await expect(bar).toHaveAttribute('data-situation-status', 'error', {
      timeout: 10_000,
    });
    // 壳照常可用（对话入口在场）——情境面绝不阻断工作台。
    await awaitShellReady(page);
  });

  test('mock：无 scope 会话 → 中性 chip 不编造', async ({ page }) => {
    const world = defaultWorld(); // 默认 situationView=null → claimed_scope none
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '分析学校分布');
    await page.waitForTimeout(1500);

    await expect(page.getByTestId('situation-status-bar')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId('situation-chip-scope')).toBeVisible({
      timeout: 10_000,
    });
    // 中性态：无分析范围（默认合成面 claimed_scope=none）。
    await expect(page.getByTestId('situation-chip-scope')).toContainText(
      /scopeNone|无分析范围|No analysis scope/,
    );
  });
});
