/**
 * Journey 42 — G42 有界目标补全面（GoalRepairPanel）。
 *
 * 断言（WP10 UI 面）：完成卡（partial）下的补全面真实可用 —— 预览动作
 * 与裁决（可执行/需确认损失/需资料）、损失确认未勾全不启动、启动后
 * 运行态 → 终态回执（真实下载链）与原终验前后 verdict 对照（partial →
 * satisfied 由服务端投影，面板不判成功）。mock 档 SSE/HTTP 档夹具按
 * 生产 goalrepair.preview/v1、goalrepair.attempt/v1 契约回放（真实后端
 * 全链由 tests/integration/test_pi_g42_goal_repair_journey.py 覆盖）。
 */
import { test, expect } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  sendChat,
  awaitShellReady,
} from '../helpers/bootstrap';
import { goalSatisfactionTurn } from '../fixtures/sse';

const PREVIEW = {
  schema_version: 'goalrepair.preview/v1',
  session_id: 's-j42',
  goal_id: 'g-j42',
  verdict: 'partial',
  signal: 'continue',
  plan_version: 'e2e0001',
  goal_fingerprint: 'fp',
  mapspec_revision: 3,
  observation_seq: 1,
  evidence_signature: 'sig-1',
  actions: [
    {
      action_id: 'a1',
      action: 'export_product',
      requirement_id: 'export:pdf',
      rule: 'export_receipt_absent',
      side_effect: 'write',
      budget_class: 'repair',
      expected_new_evidence: 'export_receipt@revision==current',
      disposition: 'executable',
      reason: '真实地图导出（blocking 语义保留）',
      entry: 'webgis_map_export',
      params: { format: 'pdf', expected_revision: 3 },
    },
    {
      action_id: 'a2',
      action: 'run_delivery',
      requirement_id: 'export:shapefile_zip',
      rule: 'delivery_receipt_absent',
      side_effect: 'write',
      budget_class: 'repair',
      expected_new_evidence: 'delivery_receipt@status==completed',
      disposition: 'needs_acknowledgment',
      reason: '数据成果交付（预览复检 + 损失确认）',
      entry: 'submit_data_delivery',
      params: { format: 'shapefile_zip', source_ref: 'ref:geojson-e2e' },
      ack_codes: ['field_name_truncated'],
    },
    {
      action_id: 'a3',
      action: 'resolve_data',
      requirement_id: 'analysis:buffer',
      rule: 'receipt_scope_subset',
      side_effect: 'read_only',
      budget_class: 'deepen',
      expected_new_evidence: 'workflow_contract@data_blockers_empty',
      disposition: 'needs_user_input',
      reason: '数据缺口需用户补充范围',
      entry: 'workflow_contract',
      params: {},
    },
  ],
  blockers: [],
  progress_rule: 'new_evidence_required_per_round',
  active_attempt: null,
  attempts: [],
};

test.describe('journey-42 goal repair panel', () => {
  test('mock：完成卡 partial 下补全面 预览→确认损失→启动→回执→终验对照', async ({ page }) => {
    const world = defaultWorld();
    world.pushChat(goalSatisfactionTurn({ sessionId: 's-j42', taskId: 't-j42' }));
    world.goalRepair.preview = PREVIEW;
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '按区统计学校数量');

    // 完成卡 + 补全面同屏（partial 才挂载面板）
    const card = page.getByTestId('goal-satisfaction-card');
    await expect(card).toHaveAttribute('data-verdict', 'partial');
    const panel = page.getByTestId('goal-repair-panel');
    await expect(panel).toBeVisible({ timeout: 20_000 });

    // 预览动作 + 裁决可读（不暴露内部 path/registry 名）
    const actions = page.getByTestId('goal-repair-action');
    await expect(actions).toHaveCount(3);
    await expect(actions.nth(0)).toHaveAttribute('data-disposition', 'executable');
    await expect(actions.nth(1)).toHaveAttribute('data-disposition', 'needs_acknowledgment');
    await expect(actions.nth(2)).toHaveAttribute('data-disposition', 'needs_user_input');
    await expect(panel).toContainText('需确认格式损失');

    // 损失确认未勾全 → 启动被前端闸拦（用户决策不代答）
    await page.getByTestId('goal-repair-start').click();
    await expect(page.getByTestId('goal-repair-error')).toContainText('确认');

    // 勾选损失确认 → 启动
    await page.getByTestId('goal-repair-ack').first().check();
    await page.getByTestId('goal-repair-start').click();

    // 运行态披露（busy 语义，重复点击不重复执行）
    await expect(panel.getByText('补全执行中', { exact: false })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('goal-repair-start')).toBeDisabled();

    // 终态：回执 + 下载链 + 原终验前后对照（服务端投影）
    const outcome = page.getByTestId('goal-repair-outcome');
    await expect(outcome).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('goal-repair-final-verdict')).toHaveText('satisfied');
    await expect(outcome).toContainText('partial → satisfied');
    const download = page.getByTestId('goal-repair-download').first();
    await expect(download).toBeVisible();
    await expect(download).toHaveAttribute('href', /\/api\/v1\/export\/download\//);

    // 请求契约：POST 携带选择与确认码（服务端重建其余一切）
    const post = world.requests.find(
      (r) => r.method === 'POST' && r.path.endsWith('/attempts'),
    );
    expect(post).toBeTruthy();
  });

  test('mock：无可执行计划（satisfied 后）不渲染补全面', async ({ page }) => {
    const world = defaultWorld();
    world.pushChat(goalSatisfactionTurn({ sessionId: 's-j42b', taskId: 't-j42b' }));
    world.goalRepair.preview = null; // 404 PLAN_NOT_FOUND 语义
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '按区统计学校数量');

    const card = page.getByTestId('goal-satisfaction-card');
    await expect(card).toBeVisible({ timeout: 20_000 });
    // satisfied 卡不挂载面板；partial 但无计划 → 面板收敛为不可用说明
    if (await card.getAttribute('data-verdict') === 'partial') {
      await expect(page.getByTestId('goal-repair-panel-unavailable')).toBeVisible({ timeout: 10_000 });
    } else {
      await expect(page.getByTestId('goal-repair-panel')).toHaveCount(0);
    }
  });
});
