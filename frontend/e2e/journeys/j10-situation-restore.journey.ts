/**
 * Journey 10 — 情境状态面：服务端投影恢复（ADR-0283 D5）@smoke.
 *
 * 刷新/重连后状态条从服务端 situation 投影恢复「当前分析范围/选中对象」
 * （localStorage 非权威）；claimed scope 有据时选择 filter 被重放（本地空白，
 * 用户现场优先）。
 */
import { test, expect } from 'playwright/test';
import { bootstrapMock, defaultWorld, awaitShellReady, sendChat } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

const SITUATION_WITH_SCOPE = {
  schema_version: 'situation_view.v1',
  session_id: 's-j1',
  situation: {},
  inconsistencies: [],
  interaction_snapshot: {
    selection: { layer_id: 'schools', selected_ids: ['sch-1', 'sch-2'] },
    attempted_not_applied: [],
  },
  selection_history: [],
  claimed_scope: {
    kind: 'feature_ids',
    layer_id: 'schools',
    dataset_ref: 'ref:geojson-j10',
    feature_ids: ['sch-1', 'sch-2'],
  },
};

test.describe('journey-10 情境状态面恢复 @smoke', () => {
  test.skip(MODE !== 'mock', REAL_ONLY_REASON);
  test('mock：刷新后状态条从服务端投影恢复分析范围与选中对象', async ({ page }) => {
    const world = defaultWorld();
    world.situationView = SITUATION_WITH_SCOPE;
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    // 建立会话（状态面在会话在场后拉取投影）。
    await sendChat(page, '分析成都学校分布');
    await page.waitForTimeout(1500);

    const bar = page.getByTestId('situation-status-bar');
    await expect(bar).toBeVisible({ timeout: 15_000 });
    await expect(bar).toHaveAttribute('data-situation-status', 'ready');
    // 分析范围 chip：2 个选中对象（服务端 claimed scope 披露）。
    const scopeChip = page.getByTestId('situation-chip-scope');
    await expect(scopeChip).toBeVisible();
    await expect(scopeChip).toContainText('2', { timeout: 10_000 });
    // 选中对象 chip 与 a11y 面（role=status + aria-live）。
    await expect(page.getByTestId('situation-chip-selection')).toBeVisible();
    await expect(bar).toHaveAttribute('role', 'status');
    await expect(bar).toHaveAttribute('aria-live', 'polite');

    // 服务端事实源断言：situation/turn-chain 端点被真实拉取（非 localStorage）。
    // （页面 reload 后 mock 壳不自动恢复会话——重放语义由
    // situation-status-bar.test.tsx 的 store 单测覆盖，此处不伪造。）
    expect(
      world.requests.some((r) => r.method === 'GET' && /\/situation(\?|$)/.test(r.path)),
    ).toBe(true);
    expect(
      world.requests.some((r) => r.method === 'GET' && /\/turn-chain(\?|$)/.test(r.path)),
    ).toBe(true);
  });
});
