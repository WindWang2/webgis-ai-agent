/**
 * Journey 7 — Chat/SSE 执行脊柱：断线重连续读 + 流式中发送保护（H03）。
 *
 * 两条真实用户路径断言（不使用 page.evaluate 直打 API —— #1555 纪律）：
 *
 * 1. 断线重连（DUP-1）：流在终态前被截断 → 前端以 Last-Event-ID 重发
 *    POST /chat/stream 续读，重放段续接在已见内容之后：最终回答包含
 *    前后两半、用户气泡恰好一条、工具行恰好一条（重放不去重会双行）。
 * 2. 流式中发送保护：turn 进行中发送键切换为『停止』形态（真实 UI 语义，
 *    #988），第二条消息无法双提交；turn 结束后发送键恢复、第二轮可用。
 */
import { test, expect } from 'playwright/test';
import { defaultWorld, installJourneyStubs } from '../fixtures/api-stubs';
import { awaitShellReady, sendChat } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

/** 带 id: 行的事件帧（DUP-1 resume 语义依赖 id 推进 seenEventIds）。 */
function idSse(id: number, name: string, payload: unknown): string {
  return `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(payload)}\n\n`;
}

const SID = 's-j7';

function partialTurn(): string {
  return [
    idSse(1, 'session', { session_id: SID }),
    idSse(2, 'task_start', { task_id: 't-j7', session_id: SID }),
    idSse(3, 'token', { content: '重连前半段回答。', session_id: SID }),
    idSse(4, 'tool_call', { name: 'hotspot_analysis', arguments: '{}', session_id: SID }),
    // 无 [DONE]、无终态事件 —— 模拟代理掐断（abrupt close → 重连判定）。
  ].join('');
}

function remainderTurn(): string {
  return [
    idSse(5, 'step_result', {
      task_id: 't-j7',
      step_id: 'sr-j7',
      tool: 'hotspot_analysis',
      geojson_ref: 'ref:j7-hotspot',
      ref_descriptor: {
        ref_id: 'ref:j7-hotspot',
        feature_count: 12,
        mvt_capable: false,
        content_revision: 1,
      },
      result: { success: true, summary: '重连后完成分析。' },
      session_id: SID,
    }),
    idSse(6, 'token', { content: '重连后半段回答。', session_id: SID }),
    idSse(7, 'task_complete', { task_id: 't-j7', session_id: SID }),
    'data: [DONE]\n\n',
  ].join('');
}

test.describe('journey-7 chat 流断线重连与发送保护', () => {
  test('mock：终态前截断 → Last-Event-ID 只读续读，内容/气泡/工具行不重复', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    await installJourneyStubs(page, world);

    // 注册顺序：后注册者优先（见 api-stubs 的 ordering 注释）——用自定义
    // 路由覆盖 chat/stream，实现「半截流 + 带头重放」两段式脚本。
    const lastEventIds: Array<string | null> = [];
    await page.route(/\/api\/v1\/chat\/stream(\?|$)/, async (route) => {
      const headers = route.request().headers();
      const lastEventId = headers['last-event-id'] ?? null;
      lastEventIds.push(lastEventId);
      const body = lastEventIds.length === 1 ? partialTurn() : remainderTurn();
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'cache-control': 'no-cache' },
        body,
      });
    });

    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '测重连');

    // 续读发生：第二次 POST 携带最后见过的事件 id 4（DUP-1 只读 resume）。
    await expect
      .poll(() => lastEventIds.length, { timeout: 20_000 })
      .toBeGreaterThanOrEqual(2);
    expect(lastEventIds[0]).toBeNull();
    expect(lastEventIds[1]).toBe('4');

    // 内容连续性：前后两半落在同一条回答气泡里，无丢失（同一文本也经
    // announcer aria-live 复制 → 取首个命中）。
    await expect(
      page.getByText(/重连前半段回答。\s*重连后半段回答。/).first(),
    ).toBeVisible({ timeout: 20_000 });

    // 无重复：用户气泡恰好一条；重放未把 tool_call 行翻倍。
    await expect(page.getByText('测重连', { exact: true })).toHaveCount(1);
    await expect(page.getByText(/hotspot_analysis/).first()).toBeVisible();
    await expect(page.locator('[data-chat-message-index]')).toHaveCount(3); // 欢迎 + 用户 + 助手

    // 重放段挂载的图层可见（step_result 生产路径真实执行）。
    await page.locator('[role="tab"][aria-label*="图层"]').first().click();
    await expect(
      page.getByRole('checkbox', { name: /分析结果/ }).first(),
    ).toBeVisible({ timeout: 20_000 });
  });

  test('mock：流式中发送键切换为停止，第二条消息无法双提交', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    world.pushChat(`id: 1\nevent: session\ndata: ${JSON.stringify({ session_id: 's-j7b' })}\n\n` +
      [
        'id: 2',
        'event: task_start',
        `data: ${JSON.stringify({ task_id: 't-j7b', session_id: 's-j7b' })}`,
        '',
        'id: 3',
        'event: token',
        `data: ${JSON.stringify({ content: '第一轮回答。', session_id: 's-j7b' })}`,
        '',
        'id: 4',
        'event: task_complete',
        `data: ${JSON.stringify({ task_id: 't-j7b', session_id: 's-j7b' })}`,
        '',
        'data: [DONE]',
        '',
      ].join('\n'));
    world.pushChat(`id: 1\nevent: session\ndata: ${JSON.stringify({ session_id: 's-j7b' })}\n\n` +
      [
        'id: 2',
        'event: task_start',
        `data: ${JSON.stringify({ task_id: 't-j7b2', session_id: 's-j7b' })}`,
        '',
        'id: 3',
        'event: token',
        `data: ${JSON.stringify({ content: '第二轮回答。', session_id: 's-j7b' })}`,
        '',
        'id: 4',
        'event: task_complete',
        `data: ${JSON.stringify({ task_id: 't-j7b2', session_id: 's-j7b' })}`,
        '',
        'data: [DONE]',
        '',
      ].join('\n'));
    await installJourneyStubs(page, world);
    // 立即 fulfill 会让 turn 瞬时完成 —— 给首个 chat 流响应加延迟，
    // 制造真实的「流式进行中」窗口来断言发送保护。
    let firstChatDeferred = false;
    await page.route(/\/api\/v1\/chat\/stream(\?|$)/, async (route) => {
      if (!firstChatDeferred) {
        firstChatDeferred = true;
        await new Promise((r) => setTimeout(r, 3000));
      }
      await route.fallback();
    });
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '第一条');

    // 流式进行中：发送键被『停止』形态替换 —— 第二条消息无法在此期间提交。
    const stop = page.getByRole('button', { name: /停止/ });
    await expect(stop).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: '发送消息' })).toHaveCount(0);
    await page.locator('textarea[aria-label="输入空间分析指令"]').fill('第二条');
    await expect(page.getByRole('button', { name: '发送消息' })).toHaveCount(0);

    // 第一轮终态到达后：发送键恢复，第二条正常提交（不双提交、不丢轮次）。
    await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible({ timeout: 20_000 });
    await page.getByRole('button', { name: '发送消息' }).click();

    await expect(
      page.getByText(/第二轮回答。/).first(),
    ).toBeVisible({ timeout: 20_000 });
    // 每条用户消息恰好一条气泡（无重复提交）。
    await expect(page.getByText('第一条', { exact: true })).toHaveCount(1);
    await expect(page.getByText('第二条', { exact: true })).toHaveCount(1);
  });

  test('real：两轮连续发送均达终态', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    await page.goto('/');
    await awaitShellReady(page);
    await sendChat(page, '第一轮');
    await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible({ timeout: 60_000 });
    await sendChat(page, '第二轮');
    await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('第一轮', { exact: true })).toHaveCount(1);
    await expect(page.getByText('第二轮', { exact: true })).toHaveCount(1);
  });
});
