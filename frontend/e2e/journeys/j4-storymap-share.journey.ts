/**
 * Journey 4 — StoryMap：会话结果 → /story 播放 → 分享链接 → 打开可回放.
 *
 * The story page reads a session by `?session_id=` and renders its messages;
 * 播放 auto-advances; 分享 copies the current URL (app/story/page.tsx
 * handleShare → clipboard). The share link opened in a fresh page must replay
 * the same story — that is the sharing contract.
 */
import { test, expect } from 'playwright/test';
import { defaultWorld, loginViaApi } from '../helpers/bootstrap';
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

async function openStory(page: import('playwright/test').Page, sessionId: string): Promise<void> {
  await page.goto(`/story?session_id=${sessionId}`);
  if (MODE === 'real') {
    // The backend compiler replaces the local-message fallback asynchronously.
    // Assert the stable compiled view, not headings that briefly appear before it.
    await expect(page.locator('[data-story-narrator]')).toBeVisible({ timeout: 20_000 });
  }
  // Both the local mock view and the compiled real view retain this heading.
  await expect(
    page.getByRole('heading', { name: '商业 POI 空间分布' }),
  ).toBeVisible({ timeout: 20_000 });
}

test.describe('journey-4 StoryMap 分享回放', () => {
  test('mock：播放推进 → 分享复制链接 → 新开链接可回放', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = storyWorld();
    await installJourneyStubs(page, world);
    await openStory(page, SESSION_ID);

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

    // 回放：分享链接在全新页面打开 → 同一会话内容再次渲染。
    const replay = await page.context().newPage();
    await installJourneyStubs(replay, storyWorld());
    await replay.goto(clip);
    await expect(
      replay.getByRole('heading', { name: '商业 POI 空间分布' }),
    ).toBeVisible({ timeout: 20_000 });
    await replay.close();
  });

  test('real：播放推进 → 分享链接回放', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // Self-seeding: create the story session through the real (stub-backed)
    // chat API — two turns produce user+assistant messages server-side; the
    // scripted LLM answers with deterministic markdown.
    const accessToken = await loginViaApi(page);
    // The compiler uses first/last message for the two-chapter fallback. Keep
    // the first prompt's heading on its own markdown line in both views.
    const turns = storySeed.map((turn) => turn.message);
    const sessionId = await seedConversationViaApi(page.request, accessToken, turns);
    await openStory(page, sessionId);
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
    const replay = await page.context().newPage();
    await replay.goto(clip);
    await expect(replay.locator('[data-story-narrator]')).toBeVisible({ timeout: 20_000 });
    await expect(
      replay.getByRole('heading', { name: '商业 POI 空间分布' }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(replay.getByRole('heading', { name: '15 分钟等时圈' })).toBeVisible();
    await replay.close();
  });
});
