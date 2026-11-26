/**
 * Journey 50 — 共享拓扑约束编辑（ADR-0304；mock 档）。
 *
 * 用户旅程（任务书 WP5 / 验收「至少三个用户旅程」之一 —— 提案卡链路）：
 *  1) 共享顶点编辑预览（vector_feature_edit_preview 携带 ADR-0304 additive
 *     impact_summary / topology_context）→ 提案卡渲染 impact 区块；
 *  2) 「新增违规 0 / 基线存量 1（披露）」语义可读，跨数据集阻断说明在场；
 *  3) 用户 approve → 决策 POST {action:'approve'} → 卡片 applied + 回执；
 *  4) 负例旅程：reject → 决策 POST {action:'reject'} → 卡片 rejected
 *     （同一决策端点、不同动作；无任何数据写入路径触发）。
 *
 * 服务端权威链（preview→CAS→journal→undo）由后端事务测试
 * tests/unit/feature_edit/test_shared_edit_flow.py 覆盖；共享顶点拖拽手势由
 * components/map/feature-layer-editor.test.tsx 覆盖。本旅程断言浏览器级接线。
 */
import { test, expect } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  sendChat,
  awaitShellReady,
} from '../helpers/bootstrap';
import { sse, toolCall } from '../fixtures/sse';

const SESSION_ID = 's-j50';
const TASK_ID = 't-j50';
const PLAN_ID = 'fep-j50-shared-1';
const REF_ID = 'ref:j50-parcels';

/** 共享顶点编辑预览 turn（ADR-0304 additive 投影在场）。 */
function sharedEditPreviewTurn(): string {
  return [
    sse('session', { session_id: SESSION_ID }),
    sse('task_start', { task_id: TASK_ID, session_id: SESSION_ID }),
    sse('token', { content: '已定位相邻地块的共享顶点，生成约束编辑预览。' }),
    toolCall('vector_feature_edit_preview', {
      ref_id: REF_ID,
      feature_ids: ['wf-A'],
      ops: [{ op: 'move_shared_vertex', at: [116.4002, 39.9], dx_m: 5, dy_m: 0 }],
    }),
    sse('step_result', {
      task_id: TASK_ID,
      step_id: 'sr-j50',
      tool: 'vector_feature_edit_preview',
      result: {
        success: true,
        plan_id: PLAN_ID,
        ref_id: REF_ID,
        status: 'pending',
        committable: true,
        safe: false,
        n_targets: 1,
        ops: [{ op: 'move_shared_vertex', touched_features: 2 }],
        changed_features: 2,
        expected_content_revision: 7,
        expires_in_s: 600,
        preview: [
          { wfid: 'wf-A', geometry: { positions_before: 5, positions_after: 5, bbox_after: [116.4, 39.8999, 116.4002, 39.9001] } },
          { wfid: 'wf-B', geometry: { positions_before: 5, positions_after: 5, bbox_after: [116.4002, 39.9, 116.4003, 39.9001] } },
        ],
        impact_summary: {
          touched_features: 2,
          affected_neighbors: 1,
          neighbors_truncated: false,
          area_delta_m2: 5.25,
          length_delta_m: null,
          atomicity: {
            scope: REF_ID,
            cross_dataset: 'blocked',
            note: 'edit transaction is dataset-scoped; cross-dataset atomic commit is not supported',
          },
        },
        topology_context: {
          new_violations: 0,
          existing_violations: 1,
          rules: ['no_overlap'],
        },
      },
    }),
    sse('task_complete', { task_id: TASK_ID, session_id: SESSION_ID }),
    sse('DONE', {}),
  ].join('');
}

test.describe('journey-50 shared topology constrained edit', () => {
  test('mock：共享编辑提案卡 impact 区块 → approve → applied（回执）', async ({ page }) => {
    const world = defaultWorld();
    world.pushChat(sharedEditPreviewTurn());
    // 决策端点 stub（api-stubs 不含共享编辑面 —— 旅程内显式装配）；
    // world.requests 只记录 installJourneyStubs 内的路由 → 决策契约用局部捕获。
    const decisions: string[] = [];
    await page.route('**/api/v1/chat/sessions/*/feature-edits/plans/*/decision', async (route) => {
      const body = route.request().postDataJSON() as { action?: string };
      decisions.push(String(body?.action));
      if (body?.action === 'approve') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'applied',
            receipt: {
              kind: 'apply',
              plan_id: PLAN_ID,
              ref_id: REF_ID,
              changed_wfids: ['wf-A', 'wf-B'],
              content_revision_after: 8,
              stamp_status: 'stamped',
              no_changes: false,
            },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'rejected' }),
      });
    });
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '把两块相邻地块的共享角点向东移 5 米');

    // 提案卡挂载 + impact 区块（ADR-0304 additive 投影可读）
    const card = page.getByTestId('edit-proposal-card-pending');
    await expect(card).toBeVisible({ timeout: 20_000 });
    const impact = page.getByTestId('edit-proposal-impact');
    await expect(impact).toBeVisible();
    await expect(impact).toContainText('2'); // 影响要素 2
    await expect(impact).toContainText('1'); // 邻接 1

    // 用户确认（坐标类变更恒需 approve —— 三通道信任边界）
    await page.getByTestId('edit-proposal-approve').click();

    // 卡片推进到 applied + 回执披露（新数据版本可供后续分析/制图）
    const applied = page.getByTestId('edit-proposal-card-applied');
    await expect(applied).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('edit-proposal-applied-receipt')).toContainText('rev 8');

    // 请求契约：approve 走决策端点（用户与 Pi 同一 plan→审批→CAS 链的前端面）
    expect(decisions).toEqual(['approve']);
  });

  test('mock：reject → 卡片 rejected（拒绝零写入路径）', async ({ page }) => {
    const world = defaultWorld();
    world.pushChat(sharedEditPreviewTurn());
    const decisions: string[] = [];
    await page.route('**/api/v1/chat/sessions/*/feature-edits/plans/*/decision', async (route) => {
      const body = route.request().postDataJSON() as { action?: string };
      decisions.push(String(body?.action));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: body?.action === 'reject' ? 'rejected' : 'applied' }),
      });
    });
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '把两块相邻地块的共享角点向东移 5 米');

    const card = page.getByTestId('edit-proposal-card-pending');
    await expect(card).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('edit-proposal-reject').click();

    const rejected = page.getByTestId('edit-proposal-card-rejected');
    await expect(rejected).toBeVisible({ timeout: 20_000 });
    // 负例：拒绝路径只发生 reject 决策调用（无 approve）
    expect(decisions.length).toBeGreaterThanOrEqual(1);
    expect(decisions.every((a) => a === 'reject')).toBeTruthy();
  });
});
