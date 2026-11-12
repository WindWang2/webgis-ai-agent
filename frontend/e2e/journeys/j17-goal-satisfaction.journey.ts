/**
 * Journey 17 — G28 任务完成语义卡（GoalSatisfactionCard）。
 *
 * 断言（A24 UI 面）：task_complete.goal_satisfaction 持久化投影在真实
 * 浏览器壳上渲染为逐需求 ledger —— 「已算出未显示」（map not_evaluated +
 * reobserve）与「统计 fulfilled」同屏可分；缺失码与建议动作无需打开
 * 开发日志即可读。mock 档 SSE 夹具按生产 bounded_payload 契约回放。
 */
import { test, expect } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  sendChat,
  awaitShellReady,
} from '../helpers/bootstrap';
import { goalSatisfactionTurn } from '../fixtures/sse';

test.describe('journey-17 goal satisfaction card', () => {
  test('mock：task_complete.goal_satisfaction 渲染逐需求完成状态卡', async ({ page }) => {
    const world = defaultWorld();
    world.pushChat(goalSatisfactionTurn({ sessionId: 's-j17', taskId: 't-j17' }));
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '按区统计学校数量');

    const card = page.getByTestId('goal-satisfaction-card');
    await expect(card).toBeVisible({ timeout: 20_000 });
    // 顶部裁决徽标 = 持久化块的 verdict（此处 partial：统计成了、图未显示）
    await expect(card).toHaveAttribute('data-verdict', 'partial');
    // 逐需求行：两条，同一持久化裁决的两面
    const rows = page.getByTestId('goal-requirement-row');
    await expect(rows).toHaveCount(2);
    // 「已算出但未显示」与「失败」可区分：map 行 not_evaluated + 建议动作
    const mapRow = rows.filter({ hasText: 'map' });
    await expect(mapRow).toContainText('not_evaluated');
    await expect(mapRow).toContainText('reobserve');
    // 统计行 fulfilled
    const statsRow = rows.filter({ hasText: 'statistics' });
    await expect(statsRow).toContainText('fulfilled');
  });
});
