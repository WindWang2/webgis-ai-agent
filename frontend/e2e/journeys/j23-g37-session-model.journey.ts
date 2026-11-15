/**
 * Journey 23 — G37 会话模型运行配置（real 档专用）。
 *
 * 真实浏览器 → 后端 → 真实 Pi（stub LLM 边界）→ 真实 dispatch → SSE → UI：
 *  1) 首轮建立会话（继承默认）；
 *  2) UI 展开管理员目录并选择 g37-b（真实 PUT/CAS）；
 *  3) 第二轮完成后：工作台按**服务端回执**显示生效 g37-b，累计 tokens 入账；
 *  4) 经 API 收紧会话 token 上限 → 第三轮被预算拒绝且在 UI 可见。
 *
 * mock 档不提供 model_status/回执夹具 —— 整条仅 real 档（诚实 skip）。
 */
import { test, expect } from 'playwright/test';
import type { Response } from 'playwright/test';
import { loginViaApi, awaitShellReady, sendChat } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON, API_ORIGIN } from '../helpers/mode';

const STREAM_PATH = /\/api\/v1\/chat\/stream$/;

function streamResponse(page: import('playwright/test').Page, timeout = 60_000): Promise<Response> {
  return page.waitForResponse(
    (r) => STREAM_PATH.test(new URL(r.url()).pathname) && r.status() === 200,
    { timeout },
  );
}

function sessionIdOf(body: string): string | null {
  return body.match(/"session_id"\s*:\s*"([^"]+)"/)?.[1] ?? null;
}

test.describe('journey-23 G37 会话模型运行配置', () => {
  test('real：目录选择→实际生效→用量入账→预算拒绝（浏览器全链）', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    const token = await loginViaApi(page);
    const authHeaders = { Authorization: `Bearer ${token}` };
    await page.goto('/');
    await awaitShellReady(page);

    // ── 轮 1：继承默认建立会话 ────────────────────────────────────
    const stream1 = streamResponse(page);
    await sendChat(page, 'g37-browser-warmup');
    const res1 = await stream1;
    const sid = sessionIdOf(await res1.text());
    expect(sid).toBeTruthy();

    const selector = page.locator('[data-testid="session-model-selector"]');
    await expect(selector).toBeVisible({ timeout: 20_000 });

    // ── 展开目录并选择 g37-b（真实 PUT/CAS via UI）──────────────
    await selector.locator('button').first().click();
    const optionB = selector.locator('button', { hasText: 'g37-b' }).first();
    await expect(optionB).toBeVisible({ timeout: 10_000 });
    await optionB.click();
    // PUT 落定 → 收起态显示显式选择 g37-b
    await expect(selector).toContainText('g37-b', { timeout: 10_000 });

    // ── 轮 2：选择生效（真实 Pi set_model + stub 实收 g37-b）─────
    const stream2 = streamResponse(page);
    await sendChat(page, 'g37-browser-turn2');
    await stream2;

    // 服务端回执真值：最近 turn effective=g37-b（轮询：回执由后台任务落盘）
    await expect
      .poll(
        async () => {
          const res = await page.request.get(
            new URL(`/api/v1/chat/sessions/${sid}/usage`, API_ORIGIN).href,
            { headers: authHeaders },
          );
          if (!res.ok()) return null;
          const data = (await res.json()) as {
            totals?: { total?: number };
            recent_turns?: Array<{ effective_model?: string | null }>;
          };
          const last = data.recent_turns?.[data.recent_turns.length - 1];
          return { total: data.totals?.total ?? 0, effective: last?.effective_model ?? null };
        },
        { timeout: 30_000 },
      )
      .toEqual({ total: expect.any(Number), effective: 'g37-b' });
    // totals.total 必须 > 0（非空转：usage 真实入账）
    const usage2 = (await (
      await page.request.get(
        new URL(`/api/v1/chat/sessions/${sid}/usage`, API_ORIGIN).href,
        { headers: authHeaders },
      )
    ).json()) as { totals: { total: number } };
    expect(usage2.totals.total).toBeGreaterThan(0);
    // UI 同步：选择器行含 g37-b（生效/选择一致性由服务端回执支撑）
    await expect(selector).toContainText('g37-b');

    // ── 预算拒绝全链：API 收紧上限 → UI 下一轮被拒且可见 ─────────
    const modelView = (await (
      await page.request.get(
        new URL(`/api/v1/chat/sessions/${sid}/model`, API_ORIGIN).href,
        { headers: authHeaders },
      )
    ).json()) as { preference: { revision: number } };
    const putRes = await page.request.put(
      new URL(`/api/v1/chat/sessions/${sid}/model`, API_ORIGIN).href,
      {
        headers: authHeaders,
        // R2-P3#4：收紧上限的同时**保留**显式选择（mode:'explicit'）——
        // 顺带验证 preference 更换写入不丢既有显式选择。
        data: {
          mode: 'explicit',
          model_id: 'g37-b',
          session_token_cap: 50,
          expected_revision: modelView.preference.revision,
        },
      },
    );
    expect(putRes.ok()).toBeTruthy();
    const putBody = (await putRes.json()) as { preference: { model_id?: string | null } };
    expect(putBody.preference.model_id).toBe('g37-b');

    const stream3 = streamResponse(page);
    await sendChat(page, 'g37-browser-budget-turn');
    const res3 = await stream3;
    const body3 = await res3.text();
    // prompt 未发出（预算拒绝）：SSE 含 policy_reject 的 task_error
    expect(body3).toContain('policy_reject');
    // UI 可见失败（错误回合通知）
    await expect
      .poll(() => page.locator('[data-chat-message-index]').last().innerText(), {
        timeout: 15_000,
      })
      .toMatch(/budget|预算/i);
  });
});
