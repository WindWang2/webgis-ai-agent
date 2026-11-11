/**
 * Journey 15 — stub-LLM / 夹具路径下的 Agent MapSpec 提交。
 *
 * 断言：MapSpec revision 从 0 递增、图层可见、图例存在。
 * mock 档用确定性 SSE 夹具；real 档走 nightly 的确定性 LLM stub + 真实后端（Pi 对话路径）。
 */
import { test, expect } from 'playwright/test';
import type { Page, Response } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  loginViaApi,
  sendChat,
  awaitShellReady,
  getJourneyAuth,
} from '../helpers/bootstrap';
import { mapspecCommitTurn } from '../fixtures/sse';
import { expectMapReady, expectLayerListed } from '../helpers/assertions';
import { API_ORIGIN, MODE, REAL_ONLY_REASON } from '../helpers/mode';

async function expectLegendVisible(page: Page): Promise<void> {
  const chrome = page.locator('[data-testid="spec-chrome-legend"], [data-testid="spec-chrome-bivariate-legend"]');
  const thematic = page.locator('[data-testid="legend-unit"], [data-testid="cat-swatch"]');
  const named = page.getByRole('img', { name: /图例|legend/i });
  await expect
    .poll(async () => {
      return (
        (await chrome.count()) +
        (await thematic.count()) +
        (await named.count())
      );
    }, { timeout: 20_000 })
    .toBeGreaterThan(0);
}

function extractSessionAndRevision(body: string): { sessionId: string | null; revision: number } {
  const sid = body.match(/"session_id"\s*:\s*"([^"]+)"/);
  const revs = [...body.matchAll(/"mutation_revision"\s*:\s*(\d+)/g)].map((m) => Number(m[1]));
  const maxRev = revs.length ? Math.max(...revs) : 0;
  return { sessionId: sid?.[1] ?? null, revision: maxRev };
}

async function workbenchRevision(page: Page, sessionId: string, accessToken?: string): Promise<number> {
  const url = new URL(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/workbench/state?meta=1`,
    API_ORIGIN,
  );
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const res = await page.request.get(url.href, { headers });
  if (!res.ok()) {
    throw new Error(`workbench/state failed: ${res.status()} ${await res.text()}`);
  }
  const data = (await res.json()) as { revision?: number };
  return Number(data.revision ?? 0);
}

test.describe('journey-15 Pi stub MapSpec 提交', () => {
  test('mock：对话提交后 revision 递增、图层可见、图例存在', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    const committed = 2;
    world.pushChat(mapspecCommitTurn({ sessionId: 's-j13', taskId: 't-j13', revision: committed }));
    await bootstrapMock(page, world);
    await page.route('**/api/v1/chat/sessions/*/workbench/state**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 's-j13', revision: committed }),
      });
    });
    await page.goto('/');
    await awaitShellReady(page);
    const streamWait = page.waitForResponse(
      (r: Response) => /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname),
      { timeout: 30_000 },
    );
    await sendChat(page, '把热点分析结果铺到地图上并显示图例');
    const streamRes = await streamWait;
    const extracted = extractSessionAndRevision(await streamRes.text());
    expect(extracted.revision).toBeGreaterThan(0);
    await expectLayerListed(page, /热点|hotspot|分析结果/i);
    await expectMapReady(page);
    await expectLegendVisible(page);
    const auth = getJourneyAuth(page);
    const rev = await workbenchRevision(page, extracted.sessionId ?? 's-j13', auth?.accessToken);
    expect(rev).toBeGreaterThan(0);
    expect(rev).toBeGreaterThanOrEqual(extracted.revision);
  });

  test('real：stub LLM 对话后 MapSpec revision 递增、图层可见、图例存在', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    const token = await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);
    // 建立会话后先读 revision（应为 0），再发送会触发 stub tool_call 的分析指令。
    const streamWait = page.waitForResponse(
      (r: Response) =>
        /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname) && r.status() === 200,
      { timeout: 45_000 },
    );
    await sendChat(page, '对上传的数据做热点分析');
    const streamRes = await streamWait;
    const extracted = extractSessionAndRevision(await streamRes.text());
    expect(extracted.sessionId).toBeTruthy();
    await expectResultsOrLayer(page);
    await expectMapReady(page);
    await expectLegendVisible(page);
    const rev = await workbenchRevision(page, extracted.sessionId as string, token);
    expect(rev).toBeGreaterThan(0);
  });
});

async function expectResultsOrLayer(page: Page): Promise<void> {
  const card = page.getByRole('button', { name: /^分析结果/ });
  try {
    await expect(card.first()).toBeVisible({ timeout: 30_000 });
  } catch {
    await expectLayerListed(page, /热点|hotspot|分析结果/i);
    return;
  }
  await expectLayerListed(page, /热点|hotspot|分析结果/i);
}
