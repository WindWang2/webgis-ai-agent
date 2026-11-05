/**
 * Journey 11 — 情境状态面六态（ADR-0283 D5 / 任务书 E4）@smoke.
 *
 * 分析范围 / 选中对象（含图表来源）/ 已验证结果 / 执行中 / 等待地图验证 /
 * 待处理可重试 六个诚实状态位同屏；「等待地图验证」来自 observed_behind_
 * desired 一致性事实（不是动画）。
 */
import { test, expect } from 'playwright/test';
import { bootstrapMock, defaultWorld, awaitShellReady, sendChat } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

test.describe('journey-11 情境六态 @smoke', () => {
  test.skip(MODE !== 'mock', REAL_ONLY_REASON);
  test('mock：六态 chip 按服务端事实同屏出现', async ({ page }) => {
    const world = defaultWorld();
    world.situationView = {
      schema_version: 'situation_view.v1',
      session_id: 's-j11',
      situation: {
        interaction: {
          pending_mutations: {
            status: 'known',
            value: [{ tool: 'buffer', status: 'running' }],
          },
        },
      },
      inconsistencies: [
        { code: 'observed_behind_desired', severity: 'info' },
      ],
      interaction_snapshot: {
        selection: { layer_id: 'districts' },
        chart_selection: { layer_id: 'districts', field: 'type', categories: ['A'] },
        attempted_not_applied: [
          { kind: 'layer_opacity_changed', layer_id: 'x', outcome: 'superseded' },
        ],
      },
      selection_history: [],
      claimed_scope: { kind: 'aoi', bbox: [104, 30.6, 104.1, 30.7] },
    };
    world.turnChainView = {
      schema_version: 'situation_turn_chain.v1',
      turns: [],
      counts: { user_explicit: 1, agent_receipts: 2, agent_pending: 1, executed_observed: 2 },
    };
    await bootstrapMock(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '分析各区学校数量');
    await page.waitForTimeout(1500);

    for (const chip of ['scope', 'selection', 'provenance', 'running', 'verifying', 'retryable']) {
      await expect(page.getByTestId(`situation-chip-${chip}`)).toBeVisible({
        timeout: 15_000,
      });
    }
    // 图表来源身份：选中对象 chip 来自 chart_selection（1 类）。
    await expect(page.getByTestId('situation-chip-selection')).toContainText('1');
    // 执行中 / 已验证结果计数来自服务端计数面。
    await expect(page.getByTestId('situation-chip-running')).toContainText('1');
    await expect(page.getByTestId('situation-chip-provenance')).toContainText('2');
  });
});
