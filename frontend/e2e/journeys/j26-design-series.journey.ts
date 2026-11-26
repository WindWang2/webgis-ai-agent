/**
 * Journey 26 — 制图规范与系列作者工作台（WP4，mock 档）。
 *
 * 真实浏览器路径（mock 契约面，stub = production client 形状）：
 *  1) Map Studio「制图」tab → 打开规范与系列工作台（薄入口弹层）；
 *  2) 规范 tab：列表（状态/版本/digest 短码）→ 选中规范 → 详情分节 + 引用校验；
 *  3) 系列 tab：系列 → 成员卡（角色/维度标签/锁定徽标）→ 一致性检查 findings；
 *  4) 传播 tab：选规范 + 目标系列（自动跑一致性门禁）→ 传播预览三列
 *     （受影响成员 / 锁定字段 / 不可解析成员）。
 *
 * 端到端（real 档）等 WP3 /api/v1/map-series 合并后补挂 —— 本档只断言契约
 * 面而不伪造后端：design-specs 后端已实现（组件测试 + real 档延伸时断言），
 * map-series 是 planning wp3_contract_brief.md 的并行契约。
 */
import { test, expect } from 'playwright/test';
import { bootstrapMock, defaultWorld, sendChat, awaitShellReady } from '../helpers/bootstrap';
import { MODE } from '../helpers/mode';

test.describe('journey-26 制图规范与系列工作台', () => {
  test('mock：面板打开 → 规范列表 → 选规范 → 系列 tab → 一致性检查 → 传播 preview', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    await bootstrapMock(page, world);

    await page.goto('/');
    await awaitShellReady(page);
    // 建立会话（stub LLM 默认回合；工作台面板本身不依赖 session，但 Studio
    // rail 与 j6/j16 同款进入路径：先有一轮会话再切 compose 模式）。
    await sendChat(page, '打开制图规范工作台');

    // 打开 Map Studio（与 j16 同款两步）。
    const modeRadio = page.getByRole('radio', { name: '制图模式' });
    await expect(modeRadio).toBeVisible({ timeout: 15_000 });
    await modeRadio.click();
    await page.locator('[role="tab"][aria-label*="制图"]').first().click();

    // ── 1. 面板打开 ────────────────────────────────────────────────────────
    await page.getByTestId('open-design-series-workbench').click();
    const modal = page.getByTestId('design-series-modal');
    await expect(modal).toBeVisible({ timeout: 15_000 });

    // ── 2. 规范列表 → 选规范（详情 + 引用校验披露）────────────────────────
    const specItem = page.getByTestId('design-series-spec-item-dspec-j26');
    await expect(specItem).toBeVisible({ timeout: 15_000 });
    await expect(specItem).toContainText('v2');
    await expect(specItem).toContainText('#j26seed0');
    await specItem.click();
    const validation = page.getByTestId('design-series-validation');
    await expect(validation).toBeVisible({ timeout: 15_000 });
    // ok 标记文案随 locale 变化 —— 以 data-ok 属性为准。
    await expect(page.getByTestId('design-series-validation-ok')).toHaveAttribute('data-ok', 'true');

    // ── 3. 系列 tab：成员卡 + 锁定徽标 → 一致性检查 ───────────────────────
    await page.getByTestId('design-series-tab-series').click();
    const seriesItem = page.getByTestId('design-series-series-item-mser-j26');
    await expect(seriesItem).toBeVisible({ timeout: 15_000 });
    await seriesItem.click();
    const card = page.getByTestId('design-series-member-card-m_2024');
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toHaveAttribute('data-locked', 'true');
    await expect(card.getByTestId('design-series-locked-badge')).toBeVisible();

    await page.getByTestId('design-series-consistency-run').click();
    const findings = page.getByTestId('design-series-findings');
    await expect(findings).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('design-series-finding-LEGEND_MISSING')).toBeVisible();

    // ── 4. 传播 tab：规范 + 目标系列（门禁自动跑一致性）→ preview 三列 ────
    await page.getByTestId('design-series-tab-propagation').click();
    await page.getByTestId('design-series-prop-spec').selectOption('dspec-j26');
    await page.getByTestId('design-series-prop-series').selectOption('mser-j26');
    // 一致性门禁先跑（stub ok=true）→ apply 可用（尚未点，仅确认门禁放行）。
    await expect(page.getByTestId('design-series-prop-apply')).toBeEnabled({ timeout: 15_000 });

    await page.getByTestId('design-series-prop-preview-run').click();
    await expect(page.getByTestId('design-series-prop-col-affected')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('design-series-prop-col-affected')).toContainText('m_2020');
    await expect(page.getByTestId('design-series-prop-col-locked')).toContainText('m_2024:palette');
    await expect(page.getByTestId('design-series-prop-col-unresolvable')).toBeVisible();
  });
});
