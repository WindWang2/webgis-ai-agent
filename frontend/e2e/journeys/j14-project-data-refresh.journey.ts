/**
 * Journey 14（原 j13，重编号避免与 G11 简报旅程冲突）— G10 项目数据刷新：检查更新 → 预览影响 → 执行候选 → 采纳/回退.
 *
 * REAL-ONLY：本旅程必须经过真实认证入口（loginViaApi，E2E_USER/E2E_PASS
 * 由 nightly lane 通过 manage.py create_admin 预置），使用合法 owner 的
 * Bearer token 调真实后端端点，再用真实 UI 走一遍旅程。
 * 不使用 fake opener / 伪成功 ACK / 跳过权限 / 截图占位。
 *
 * 前置（runner 注入，缺失则显式 skip 并给出理由，绝不静默绿）：
 *   E2E_G10_PROJECT_ID  已存在且属于该 owner 的项目
 *   E2E_G10_SESSION_ID  该 owner 拥有的活动会话
 *
 * 工具成功 / 任务执行成功 / 地图观察 / 版本采纳分别断言，不互相替代；
 * map_observation 未观察时保持 unverified，不宣称「地图已正确更新」。
 */
import { test, expect } from 'playwright/test';
import { loginViaApi, awaitShellReady } from '../helpers/bootstrap';
import { API_ORIGIN, MODE, REAL_ONLY_REASON } from '../helpers/mode';

const PROJECT_ID = process.env.E2E_G10_PROJECT_ID ?? '';
const SESSION_ID = process.env.E2E_G10_SESSION_ID ?? '';

test.describe('journey-14 项目数据刷新', () => {
  test('real：检查→预览→执行→比较→采纳→回退（真实认证 + 真实后端 + 真实 UI）', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    test.skip(!PROJECT_ID || !SESSION_ID,
      'real G10 journey requires E2E_G10_PROJECT_ID and E2E_G10_SESSION_ID');

    const token = await loginViaApi(page);
    const headers = { Authorization: `Bearer ${token}` };
    const root = `${API_ORIGIN}/api/v1/projects/${encodeURIComponent(PROJECT_ID)}/data-refresh`;

    // 1) 首次检查更新（建立基线，不声称变化）
    const first = await page.request.post(`${root}/check`, {
      headers,
      data: { session_id: SESSION_ID, establish_baseline: true },
    });
    expect(first.ok(), `check failed: ${first.status()} ${await first.text()}`).toBeTruthy();
    const firstBody = await first.json();
    expect(firstBody.sources_total).toBeGreaterThanOrEqual(0);
    expect(Array.isArray(firstBody.inventory.items)).toBeTruthy();

    // 2) 只读预览：不写 MapSpec / 数据 / 基线
    const preview = await page.request.post(`${root}/preview`, {
      headers, data: { session_id: SESSION_ID, style_only: false },
    });
    expect(preview.ok(), `preview failed: ${preview.status()} ${await preview.text()}`).toBeTruthy();
    const proposal = await preview.json();
    expect(proposal.proposal_fingerprint).toMatch(/^g10:/);
    expect(Array.isArray(proposal.plan.recompute)).toBeTruthy();
    const proposalId = proposal.proposal_id as string;

    // 3) 状态可恢复（刷新页面后候选/进度仍在服务端）
    const status = await page.request.get(`${root}/proposals/${proposalId}/status`, { headers });
    expect(status.ok()).toBeTruthy();
    expect((await status.json()).recoverable).toBe(true);

    // 4) 真实 UI：项目资产 → 数据更新 tab → 检查更新 / 预览影响
    await page.goto('/');
    await awaitShellReady(page);
    await page.locator('[role="tab"]').filter({ hasText: '项目' }).first().click();
    await page.locator('[data-testid="g10-data-refresh-panel"]').waitFor({ timeout: 20_000 });
    await page.getByTestId('g10-check-updates').click();
    await expect(page.getByTestId('g10-check-summary')).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('g10-preview').click();
    await expect(page.getByTestId('g10-recompute-plan')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/read-only|只读|只读预览/)).toBeVisible();

    // 5) 执行隔离候选：当前成果版本不变
    const execute = await page.request.post(`${root}/proposals/${proposalId}/execute`, {
      headers, data: { cancel: false },
    });
    expect(execute.ok(), `execute failed: ${execute.status()} ${await execute.text()}`).toBeTruthy();
    const executed = await execute.json();
    expect(executed.receipt.version_adoption).toBe('unchanged');
    expect(executed.receipt.map_observation).toBe('unverified');

    // 6) 候选对比
    const compare = await page.request.get(`${root}/proposals/${proposalId}/compare`, { headers });
    expect(compare.ok()).toBeTruthy();
    const compared = await compare.json();
    expect(Array.isArray(compared.preserved_user_edits)).toBeTruthy();
    expect(Array.isArray(compared.conflicts)).toBeTruthy();

    // 7) 原子采纳（服务端双核对 revision + proposal fingerprint）
    const adopt = await page.request.post(`${root}/proposals/${proposalId}/adopt`, {
      headers,
      data: {
        expected_revision: proposal.mapspec_revision,
        proposal_fingerprint: proposal.proposal_fingerprint,
      },
    });
    // 漂移/冲突是合法拒绝；此处要求不是 5xx，且成功时 revision 单调增
    expect(adopt.status()).toBeLessThan(500);
    const adopted = await adopt.json();
    if (adopted.status === 'adopted') {
      expect(adopted.revision_after).toBeGreaterThan(adopted.revision_before);
      expect(adopted.map_observation).toBe('unverified');
      expect(adopted.version_adoption).toBe('adopted');

      // 8) 回退：新 revision，不倒写、不改不可变快照
      const rollback = await page.request.post(
        `${root}/proposals/${proposalId}/rollback`, { headers, data: {} });
      expect(rollback.status()).toBeLessThan(500);
      const rolled = await rollback.json();
      expect(rolled.revision_after).toBeGreaterThanOrEqual(adopted.revision_after);
      expect(rolled.map_observation).toBe('unverified');
    }
  });
});
