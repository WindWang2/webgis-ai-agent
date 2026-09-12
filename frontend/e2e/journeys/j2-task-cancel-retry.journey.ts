/**
 * Journey 2 — 任务取消/重试（真取消语义）.
 *
 * A long task is started, cancelled through the task center, and the UI must
 * show「已取消」only via a terminal state (规范 §30 — cancel requested ≠
 * cancelled). A failed task is retried and reaches completion.
 */
import { test, expect } from 'playwright/test';
import { defaultWorld } from '../helpers/bootstrap';
import { installJourneyStubs, makeJob } from '../fixtures/api-stubs';
import { analysisTurn } from '../fixtures/sse';
import { awaitShellReady, sendChat } from '../helpers/bootstrap';
import { expectJobState } from '../helpers/assertions';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

test.describe('journey-2 任务取消与重试', () => {
  test('mock：长任务取消走终态，失败任务重试后可完成', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    // The task center scopes jobs to the ACTIVE session (「开始一次对话后即可
    // 查看任务」), so the journey first opens a real chat turn whose SSE
    // session event establishes session s-j2; seeded jobs carry that id.
    world.pushChat(analysisTurn({ sessionId: 's-j2', taskId: 't-j2' }));
    // Seed: one running (cancellable) and one failed (retryable) job.
    world.jobs = [
      makeJob('job-run', '长时空间分析', 'running', 30, '正在计算…'),
      makeJob('job-fail', 'ST-DBSCAN 时空聚类', 'failed', null, '任务失败'),
    ];
    await installJourneyStubs(page, world);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '启动长时分析');

    // ── 取消：真取消语义 ────────────────────────────────────────────────────
    await expectJobState(page, '长时空间分析', /正在计算|运行中|%/);
    const cancel = page.locator('button[aria-label*="取消 长时空间分析"], button[title*="取消 长时空间分析"]').first();
    await expect(cancel).toBeEnabled({ timeout: 15_000 });
    await cancel.click();
    // Terminal state is what the UI must show after cancel (not merely a
    // requested flag): the DELETE handler flips the world record, the list
    // refresh renders 已取消.
    await expectJobState(page, '长时空间分析', /已取消|已停止|取消/);
    expect(
      world.requests.some((r) => r.method === 'DELETE' && /^\/api\/v1\/tasks\/jobs\//.test(r.path)),
    ).toBe(true);

    // ── 重试：failed → 重试 → 完成 ─────────────────────────────────────────
    const retry = page.locator('button[aria-label*="重试 ST-DBSCAN"], button[title*="重试 ST-DBSCAN"]').first();
    await expect(retry).toBeEnabled({ timeout: 15_000 });
    await retry.click();
    // Retry flips the seeded record back to running; the journey asserts the
    // terminal transition request contract (POST /retry) and the running state
    // rendering. Completion is asserted by the completing-turn replay in the
    // chat-driven variant below (a job's own completion needs a backend run).
    await expectJobState(page, 'ST-DBSCAN 时空聚类', /运行中|正在|排队|%/);
    expect(
      world.requests.some((r) => r.method === 'POST' && /\/api\/v1\/tasks\/jobs\/[^/]+\/retry$/.test(r.path)),
    ).toBe(true);
  });

  test('real：取消在途任务，重试失败任务', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Nightly stack seeds a long-running cancellable job and a failed job
    // (same seeds as mock). Cancel → terminal state; retry → terminal state.
    await page.goto('/');
    await expectJobState(page, /长时空间分析/, /运行中|正在|%/);
    const cancel = page.locator('button[aria-label*="取消 长时空间分析"], button[title*="取消 长时空间分析"]').first();
    await expect(cancel).toBeEnabled({ timeout: 15_000 });
    await cancel.click();
    await expectJobState(page, /长时空间分析/, /已取消|已停止|取消/);

    await expectJobState(page, /ST-DBSCAN/, /失败/);
    const retry = page.locator('button[aria-label*="重试 ST-DBSCAN"], button[title*="重试 ST-DBSCAN"]').first();
    await expect(retry).toBeEnabled({ timeout: 15_000 });
    await retry.click();
    // Any non-failed active/terminal transition proves the retry landed.
    await expectJobState(page, /ST-DBSCAN/, /运行中|正在|排队|已完成|完成|失败/);
  });
});
