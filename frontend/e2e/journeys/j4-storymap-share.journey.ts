/**
 * Journey 4 — StoryMap：会话结果 → /story 播放 → 分享链接 → 打开可回放.
 *
 * The story page reads a session by `?session_id=` and renders its messages;
 * 播放 auto-advances; 分享 copies the current URL (app/story/page.tsx
 * handleShare → clipboard). The share link opened in a fresh page must replay
 * the same story — that is the sharing contract.
 */
import { test, expect } from 'playwright/test';
import { defaultWorld } from '../helpers/bootstrap';
import { installJourneyStubs } from '../fixtures/api-stubs';
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
  // 第一条 assistant 消息的 markdown 标题渲染出来即视为故事加载完成。
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
    // Nightly stack seeds session s-j4 with story messages (same seed).
    await openStory(page, SESSION_ID);
    const play = page.locator('button[aria-label="播放"]').first();
    await expect(play).toBeEnabled({ timeout: 15_000 });
    await play.click();
    await expect(page.locator('button[aria-label="暂停"]').first()).toBeVisible({ timeout: 15_000 });
    const share = page.locator('button[aria-label="分享"]').first();
    await share.click();
    const toast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: '已复制分享链接' });
    await expect(toast.first()).toBeVisible({ timeout: 15_000 });
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain(`session_id=${SESSION_ID}`);
    const replay = await page.context().newPage();
    await replay.goto(clip);
    await expect(
      replay.getByRole('heading', { name: '商业 POI 空间分布' }),
    ).toBeVisible({ timeout: 20_000 });
    await replay.close();
  });
});
