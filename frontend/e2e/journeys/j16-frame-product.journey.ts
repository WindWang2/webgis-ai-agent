/**
 * Journey 16 — G15 多画幅产品工作台旅程（mock 档）。
 *
 * 真实浏览器路径：Agent 对话提交三画幅产品（SSE 夹具 = 服务端 bind 后的
 * spec 投影）→ Map Studio「画幅」子页签恢复三幅 + 共享分级摘要 →
 * 用户改第二幅标题（键级 CAS patch，frame_id 身份不变）→ 激活第三幅。
 */
import { test, expect, type Response } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  sendChat,
  awaitShellReady,
} from '../helpers/bootstrap';
import { frameProductCommitTurn } from '../fixtures/sse';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

interface SentMutation {
  intent: string;
  expected_revision: number;
  patch: { active_frame?: string; frames?: Array<{ frame_id: string; title?: string }> };
}

test.describe('journey-16 多画幅产品工作台', () => {
  test('mock：三画幅恢复 · 改名 CAS · 激活画幅', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    world.pushChat(frameProductCommitTurn({ sessionId: 's-j14', revision: 3 }));
    await bootstrapMock(page, world);
    await page.route('**/api/v1/chat/sessions/*/workbench/state**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 's-j14', revision: 3 }),
      });
    });
    // 记录面板提交的 mutation（放行到 404 mock —— 只断言请求体）。
    const sentMutations: SentMutation[] = [];
    await page.route('**/api/v1/chat/sessions/*/mapspec/mutations', async (route) => {
      const body = route.request().postDataJSON() as SentMutation;
      sentMutations.push(body);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', mutation_revision: 4 }),
      });
    });

    await page.goto('/');
    await awaitShellReady(page);
    const streamWait = page.waitForResponse(
      (r: Response) => /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname),
      { timeout: 30_000 },
    );
    await sendChat(page, '把成都各区每万人小学学位按 2020/2022/2024 并排做成三幅图');
    await streamWait;

    // 打开 Map Studio：先切 compose 模式（制图 tab 仅在该模式的 rail），
    // 再点「制图」tab（与 j1 同款两步）。
    const modeRadio = page.getByRole('radio', { name: '制图模式' });
    await expect(modeRadio).toBeVisible({ timeout: 15_000 });
    await modeRadio.click();
    await page.locator('[role="tab"][aria-label*="制图"]').first().click();
    // 画幅子页签（Studio 三段子页签之一）。
    await page.locator('#map-studio-tab-frames').click();
    const framesList = page.locator('[data-testid="frame-product-frames"]');
    await expect(framesList).toBeVisible({ timeout: 15_000 });
    await expect(framesList.locator('li')).toHaveCount(3);
    // 顺序 = 显示序；身份键在 data-frame-id。
    await expect(framesList.locator('li').nth(0)).toHaveAttribute('data-frame-id', 'fp-t-2020');
    await expect(framesList.locator('li').nth(2)).toHaveAttribute('data-frame-id', 'fp-t-2024');
    // 共享分级摘要可见（field + 全域已计算）。
    const shared = page.locator('[data-testid="frame-product-shared"]');
    await expect(shared).toContainText('per_10k');

    // 改第二幅标题（blur 提交）→ 键级 patch + CAS。
    const titleInputs = page.getByLabel('画幅标题');
    await titleInputs.nth(1).fill('2022（更新标题）');
    await titleInputs.nth(1).blur();
    await expect
      .poll(() => sentMutations.length, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(1);
    const rename = sentMutations[0];
    expect(rename.intent).toBe('set_frame_product');
    expect(rename.expected_revision).toBeGreaterThan(0);
    expect(rename.patch.frames?.[1]).toMatchObject({
      frame_id: 'fp-t-2022',
      title: '2022（更新标题）',
    });
    // frame_id 身份与顺序保持。
    expect(rename.patch.frames?.map((f) => f.frame_id)).toEqual([
      'fp-t-2020', 'fp-t-2022', 'fp-t-2024',
    ]);

    // 激活第三幅。
    await page.getByLabel('激活画幅 fp-t-2024').click();
    await expect
      .poll(() => sentMutations.length, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(2);
    expect(sentMutations[1].patch.active_frame).toBe('fp-t-2024');
  });

  test('real：占位（real 档 G15 旅程待 Pi stub 驱动 frame_product_bind）', async () => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // real 档需要确定性 LLM stub 产出 frame_product_bind 工具调用 ——
    // 本 PR 的工具面已具备（frame_product_bind），nightly 短语接入后启用。
    expect(true).toBe(true);
  });
});
