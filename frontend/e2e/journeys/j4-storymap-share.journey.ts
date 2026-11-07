/**
 * Journey 4 — StoryMap：会话结果 → /story 播放 → 分享链接 → 打开可回放.
 *
 * The story page reads a session by `?session_id=` and renders its messages;
 * 播放 auto-advances; 分享 copies the current URL (app/story/page.tsx
 * handleShare → clipboard). The share link opened in a fresh page must replay
 * the same story — that is the sharing contract.
 */
import { test, expect, type Page } from 'playwright/test';
import { awaitShellReady, defaultWorld, getJourneyAuth, loginViaApi, seedAuthUser } from '../helpers/bootstrap';
import { seedConversationViaApi } from '../helpers/real-chat';
import { installJourneyStubs } from '../fixtures/api-stubs';
import storySeed from '../fixtures/story-seed.json';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

const SESSION_ID = 's-j4';

function storyWorld() {
  const world = defaultWorld();
  world.sessionDetail = {
    session_id: SESSION_ID,
    title: 'StoryMap 旅程会话',
    messages: [
      { role: 'user', content: '分析北京商业 POI 的空间分布', created_at: '2026-01-01T08:00:00Z' },
      {
        role: 'assistant',
        content: '## 商业 POI 空间分布\n\n热点集中在城市中心，**12 个显著热点**。\n\n- 第一段结论\n- 第二段结论',
        created_at: '2026-01-01T08:01:00Z',
      },
      { role: 'user', content: '生成等时圈', created_at: '2026-01-01T08:02:00Z' },
      { role: 'assistant', content: '## 15 分钟等时圈\n\n覆盖 3 个商圈，可达性良好。', created_at: '2026-01-01T08:03:00Z' },
    ],
  };
  return world;
}

/** Restore the owner's session through History so the production responder
 * is scoped to that exact session. No token-store or session-cursor writes. */
async function restoreWorkspace(page: Page, sessionId: string): Promise<void> {
  await page.goto('/');
  await awaitShellReady(page);
  const listing = page.waitForResponse((response) =>
    response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/v1/chat/sessions');
  await page.getByRole('button', { name: '历史记录', exact: true }).click();
  const response = await listing;
  expect(response.status()).toBe(200);
  const { sessions } = await response.json() as { sessions: Array<{ id: string; title: string }> };
  const index = sessions.findIndex((session) => session.id === sessionId);
  expect(index, 'the seeded owner session must be listed').toBeGreaterThanOrEqual(0);
  const drawer = page.locator('[role="dialog"][aria-labelledby="history-drawer-title"]');
  const rows = drawer.getByRole('listitem');
  await expect(rows).toHaveCount(sessions.length);
  const selected = page.waitForResponse((item) =>
    item.request().method() === 'GET' && new URL(item.url()).pathname === `/api/v1/chat/sessions/${sessionId}`);
  await rows.nth(index).getByRole('button').first().click();
  expect((await selected).status()).toBe(200);
  await expect(drawer).not.toBeVisible();
  await expect(page.getByText('15 分钟等时圈', { exact: false }).first()).toBeVisible();
}

/** A real UI-created popup supplies a real WindowProxy as message source. */
async function openStory(page: Page, sessionId: string): Promise<Page> {
  await page.keyboard.press('ControlOrMeta+k');
  await page.getByTestId('command-palette-input').fill('在故事视图打开当前会话');
  const popup = page.waitForEvent('popup');
  const sessionResponse = page.context().waitForEvent('response', { predicate: (response) =>
    response.request().method() === 'GET' && new URL(response.url()).pathname === `/api/v1/chat/sessions/${sessionId}` });
  await page.getByRole('option', { name: /在故事视图打开当前会话/ }).click();
  const story = await popup;
  const response = await sessionResponse;
  expect(response.request().frame().page()).toBe(story);
  expect(response.request().headers().authorization).toBe(`Bearer ${getJourneyAuth(page)!.accessToken}`);
  expect(response.status()).toBe(200);
  expect(await story.opener()).toBe(page);
  await expectStory(story);
  return story;
}

async function expectStory(page: Page): Promise<void> {
  if (MODE === 'real') {
    // Assert the stable compiled view, not transient local fallback headings.
    await expect(page.locator('[data-story-narrator]')).toBeVisible({ timeout: 20_000 });
  }
  await expect(page.getByRole('heading', { name: '商业 POI 空间分布' })).toBeVisible({ timeout: 20_000 });
}

/** The copied link is owner-bound, not a public authorization grant. Reload it
 * in another UI-opened Story tab; the owner's workspace performs a new real
 * handshake after navigation (no credential copy, URL token, or fake opener). */
async function replayLink(workspace: Page, sessionId: string, clip: string): Promise<void> {
  const replay = await openStory(workspace, sessionId);
  const sessionResponse = replay.waitForResponse((response) =>
    response.request().method() === 'GET' && new URL(response.url()).pathname === `/api/v1/chat/sessions/${sessionId}`);
  await replay.goto(clip);
  const response = await sessionResponse;
  expect(response.request().headers().authorization).toBe(`Bearer ${getJourneyAuth(workspace)!.accessToken}`);
  expect(response.status()).toBe(200);
  await expectStory(replay);
  await expect(replay.getByRole('heading', { name: '15 分钟等时圈' })).toBeVisible();
  await replay.close();
}

test.describe('journey-4 StoryMap 分享回放', () => {
  test('@smoke mock：真实 opener 认证 → 播放推进 → 分享链接回放', async ({ page: workspace }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = storyWorld();
    await seedAuthUser(workspace);
    // Context routes cover the UI-created popup from its very first request.
    await installJourneyStubs(workspace.context(), world);
    await restoreWorkspace(workspace, SESSION_ID);
    const page = await openStory(workspace, SESSION_ID);

    // 播放：逐条自动推进（播放按钮切换为暂停即处于播放态）。
    const play = page.locator('button[aria-label="播放"]').first();
    await expect(play).toBeEnabled({ timeout: 15_000 });
    await play.click();
    await expect(page.locator('button[aria-label="暂停"]').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('story-scrubber-range')).toHaveValue('1');

    // 分享：复制当前 URL（grant 了 clipboard 权限）。
    const share = page.locator('button[aria-label="分享"]').first();
    await share.click();
    const toast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: '已复制分享链接' });
    await expect(toast.first()).toBeVisible({ timeout: 15_000 });
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('/story');
    expect(clip).toContain(`session_id=${SESSION_ID}`);

    await replayLink(workspace, SESSION_ID, clip);
    await page.close();
  });

  test('real：播放推进 → 分享链接回放', async ({ page: workspace }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Self-seeding: create the story session through the real (stub-backed)
    // chat API — two turns produce user+assistant messages server-side; the
    // scripted LLM answers with deterministic markdown.
    const accessToken = await loginViaApi(workspace);
    // The compiler uses first/last message for the two-chapter fallback. Keep
    // the first prompt's heading on its own markdown line in both views.
    const turns = storySeed.map((turn) => turn.message);
    const sessionId = await seedConversationViaApi(workspace.request, accessToken, turns);
    await restoreWorkspace(workspace, sessionId);
    const page = await openStory(workspace, sessionId);
    await expect(page.getByRole('heading', { name: '15 分钟等时圈' })).toBeVisible();
    const play = page.locator('button[aria-label="播放"]').first();
    await expect(play).toBeEnabled({ timeout: 15_000 });
    await play.click();
    await expect(page.locator('button[aria-label="暂停"]').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('story-scrubber-range')).toHaveValue('1');
    const share = page.locator('button[aria-label="分享"]').first();
    await share.click();
    const toast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: '已复制分享链接' });
    await expect(toast.first()).toBeVisible({ timeout: 15_000 });
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    // The real leg seeds its session through the chat API, so the id is only
    // known at runtime — asserting the mock world's constant here would be a
    // guaranteed red (or a vacuous pass if the constant ever leaked into real).
    expect(clip).toContain('/story');
    expect(clip).toContain(`session_id=${sessionId}`);
    await replayLink(workspace, sessionId, clip);
    // A copied URL alone must not bypass session ownership.
    const outsider = await workspace.context().newPage();
    const denied = outsider.waitForResponse((response) =>
      response.request().method() === 'GET' && new URL(response.url()).pathname === `/api/v1/chat/sessions/${sessionId}`);
    await outsider.goto(clip);
    const response = await denied;
    expect(response.request().headers().authorization).toBeUndefined();
    expect([401, 403, 404]).toContain(response.status());
    await expect(outsider.getByRole('heading', { name: '商业 POI 空间分布' })).not.toBeVisible();
    await outsider.close();
    await page.close();
  });
});
