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
import { awaitShellReady, loginViaApi, sendChat } from '../helpers/bootstrap';
import { expectJobState, waitForChatStreamResponse } from '../helpers/assertions';
import { API_ORIGIN, MODE, REAL_ONLY_REASON } from '../helpers/mode';
import { runRetryFixture } from '../helpers/real-jobs';

test.describe('journey-2 任务取消与重试', () => {
  test('mock：长任务取消走终态，失败任务重试后重新运行', async ({ page }) => {
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
    // rendering. Completion and the generated raster are asserted by the real worker
    // variant below (a job's own completion needs a backend run).
    await expectJobState(page, 'ST-DBSCAN 时空聚类', /运行中|正在|排队|%/);
    expect(
      world.requests.some((r) => r.method === 'POST' && /\/api\/v1\/tasks\/jobs\/[^/]+\/retry$/.test(r.path)),
    ).toBe(true);
  });

  test('real：取消在途任务，重试失败任务', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);
    // Only the external LLM boundary waits. Auth, session creation, the live
    // agent task, cancellation and durable retry all use the real backend.
    const streamResponsePromise = waitForChatStreamResponse(page);
    await sendChat(page, '启动可取消的任务旅程');
    const streamResponse = await streamResponsePromise;
    await expectJobState(page, 'AI 任务', '运行中');
    const agentCard = page.locator('[data-testid^="job-card-task-"]').first();
    const agentId = (await agentCard.getAttribute('data-testid'))!.replace('job-card-', '');
    const auth = await page.evaluate(() => JSON.parse(localStorage.getItem('webgis_auth') ?? '{}')) as {
      accessToken: string; user: { id: string };
    };
    expect(auth.accessToken).toBeTruthy();
    const headers = { Authorization: `Bearer ${auth.accessToken}` };
    const jobUrl = (id: string) => new URL(`/api/v1/tasks/jobs/${id}`, API_ORIGIN).href;
    const liveResponse = await page.request.get(jobUrl(agentId), { headers });
    expect(liveResponse.status()).toBe(200);
    const live = await liveResponse.json();
    expect(live).toMatchObject({ id: agentId, kind: 'agent', status: 'running', active: true });
    expect(live.session_id).toBeTruthy();

    const cancellation = page.waitForResponse((response) =>
      response.request().method() === 'DELETE' && response.url() === jobUrl(agentId));
    await agentCard.getByRole('button', { name: /^取消 / }).click();
    const cancelResponse = await cancellation;
    expect(cancelResponse.status()).toBe(200);
    expect(await cancelResponse.json()).toMatchObject({ id: agentId, cancel_requested: true });
    // A DELETE acknowledgement or “取消中” is not terminal cancellation.
    await expect(agentCard.getByText('已取消', { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(agentCard).toHaveAttribute('aria-busy', 'false');
    const cancelled = await page.request.get(jobUrl(agentId), { headers });
    expect(cancelled.status()).toBe(200);
    expect(await cancelled.json()).toMatchObject({ status: 'cancelled', active: false, retryable: false });
    // The live stream must stop too: a terminal badge alone used to hide an
    // upstream LLM read that continued and eventually reported success.
    const cancelledStream = await streamResponse.text();
    const eventNames = [...cancelledStream.matchAll(/^event:\s*(\S+)/gm)].map((match) => match[1]);
    expect(eventNames.slice(-2)).toEqual(['task_cancelled', 'done']);
    expect(eventNames.filter((name) => name === 'task_cancelled')).toHaveLength(1);
    expect(eventNames).not.toContain('task_complete');

    // Submit the real NDVI worker with a missing input and restore its tiny
    // GeoTIFF only after attempt 1 really fails. Ownership is checked against
    // this exact authenticated, UI-created conversation by the local CLI.
    const fixture = await runRetryFixture('prepare', live.session_id, auth.user.id);
    expect(fixture).toMatchObject({ session_id: live.session_id, status: 'failed', attempt: 1 });
    const retryId = String(fixture.job_id);
    await page.getByRole('button', { name: '刷新任务', exact: true }).click();
    const retryCard = page.getByTestId(`job-card-${retryId}`);
    await expect(retryCard.getByText('失败', { exact: true })).toBeVisible();
    const retryResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && response.url() === `${jobUrl(retryId)}/retry`);
    await retryCard.getByRole('button', { name: /^重试 / }).click();
    const retryResponse = await retryResponsePromise;
    expect(retryResponse.status()).toBe(200);
    expect(await retryResponse.json()).toMatchObject({ id: retryId, retried: true, attempt: 2 });
    // Neither queued/running nor failed can satisfy completion.
    await expect(retryCard.getByText('已完成', { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(retryCard).toHaveAttribute('aria-busy', 'false');
    const completed = await page.request.get(jobUrl(retryId), { headers });
    expect(completed.status()).toBe(200);
    expect(await completed.json()).toMatchObject({ status: 'completed', attempt: 2, active: false });
    expect(await runRetryFixture('verify', live.session_id, auth.user.id, retryId)).toMatchObject({
      job_id: retryId, status: 'completed', attempt: 2, valid_pixels: 256, ndvi: 0.5,
    });
  });
});
