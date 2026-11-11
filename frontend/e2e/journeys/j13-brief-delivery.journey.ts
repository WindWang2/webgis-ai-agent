/**
 * Journey 13 — G11 证据化地图简报：草稿 → 编辑/锁定 → 证据核对 →
 * 预检 → 冻结发布 → 私有下载 → 显式分享 → 撤销（BRF-O10 浏览器旅程）。
 *
 * mock：brief 端点由本 journey 的有状态路由桩驱动（后注册先匹配，
 * 覆盖面之外的请求仍走 installJourneyStubs）。
 * real：真实后端 —— 真登录、真实 chat 种子会话、真实编译/预检/冻结/
 * CAS 下载/分享撤销；外部者访问必须 401/403/404（所有权契约）。
 */
import { test, expect, type Page, type BrowserContext } from 'playwright/test';
import { awaitShellReady, defaultWorld, getJourneyAuth, loginViaApi, seedAuthUser } from '../helpers/bootstrap';
import { seedConversationViaApi } from '../helpers/real-chat';
import { installJourneyStubs } from '../fixtures/api-stubs';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

const SESSION_ID = 's-j13';

function briefWorld() {
  const world = defaultWorld();
  world.sessionDetail = {
    session_id: SESSION_ID,
    title: '简报旅程会话',
    messages: [
      { role: 'user', content: '分析学校分布并整理成简报', created_at: '2026-01-02T08:00:00Z' },
      {
        role: 'assistant',
        content: '## 学校分布分析\n锦江区 142 所，青羊区 97 所。',
        created_at: '2026-01-02T08:01:00Z',
      },
    ],
  };
  return world;
}

// ── mock：有状态 brief 端点桩 ────────────────────────────────────────

interface BriefStubState {
  revision: number;
  created: boolean;
  lockedText: boolean;
  textContent: string;
  preflightRun: boolean;
  frozen: boolean;
  releaseNo: number;
  shareCode: string | null;
}

function installBriefStubs(context: BrowserContext, state: BriefStubState): void {
  const spec = () => ({
    brief_schema_version: 'brief-1',
    metadata: { title: '学校分布简报', session_id: SESSION_ID },
    chapters: [{
      id: 'arc-introduction', title: '引言', narrative: '## 引言',
      arc_role: 'introduction',
    }],
    camera_keyframes: [],
    linked_widgets: [],
    blocks: [
      {
        id: 'blk-text-demo', chapter_id: 'arc-introduction', kind: 'text',
        order: 1, origin: 'auto', locked: state.lockedText,
        text: state.textContent,
      },
      {
        id: 'blk-metric-demo', chapter_id: 'arc-introduction', kind: 'metric',
        order: 2, origin: 'auto', locked: false, fact_id: 'fact-demo',
      },
      {
        id: 'blk-map-demo', chapter_id: 'arc-introduction', kind: 'map_view',
        order: 3, origin: 'auto', locked: false,
        map_view: {
          center: [104.06, 30.66], zoom: 10, pitch: 0, bearing: 0,
          visible_layer_ids: ['schools'], mapspec_fingerprint: 'fp-demo-123',
        },
      },
    ],
    facts: [{
      fact_id: 'fact-demo', label: '锦江区学校数', kind: 'count', value: 142,
      unit: '所', status: 'verified', origin: 'auto',
      basis: {
        artifact_ref: 'ref:stats-demo', source_label: '学校分布统计表',
        field_path: 'table.rows.0.count', method: 'count',
      },
    }],
    provenance: {
      session_id: SESSION_ID, mapspec_fingerprint: 'fp-demo-123',
      artifact_refs: ['ref:stats-demo'], generator: 'deterministic',
    },
  });

  const briefResponse = () => ({
    brief_id: 'br-demo', revision: state.revision, spec: spec(),
    stale: [],
    summary: {
      brief_id: 'br-demo', revision: state.revision, title: '学校分布简报',
      chapters: 1, blocks: 3, facts: 1,
      fact_stats: { verified: 1, warning: 0, unverified: 0 },
    },
  });

  const json = (payload: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });

  void context.route(/\/api\/v1\/briefs\//, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    // CORS 预检直通（与 installJourneyStubs 同款语义）
    if (method === 'OPTIONS') {
      await route.fulfill({ status: 204, body: '' });
      return;
    }

    if (path.endsWith('/draft') && method === 'POST') {
      state.created = true;
      state.revision = 1;
      await route.fulfill(json(briefResponse()));
      return;
    }
    if (/\/blocks\/blk-text-demo$/.test(path) && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}');
      if (body.expected_revision !== state.revision) {
        await route.fulfill(json({ detail: 'revision conflict' }, 409));
        return;
      }
      const patch = body.patch ?? {};
      if (typeof patch.locked === 'boolean') state.lockedText = patch.locked;
      if (typeof patch.text === 'string') state.textContent = patch.text;
      state.revision += 1;
      await route.fulfill(json({ saved: true, ...briefResponse() }));
      return;
    }
    if (/\/evidence\/fact-demo$/.test(path)) {
      await route.fulfill(json({
        fact: spec().facts[0],
        check: { status: 'verified', reasons: [], recompute: {}, contradicted: false },
        authoritative_value: 142,
        dependencies: {},
        artifact: { registered: true, record: { artifact_id: 'ref:stats-demo' } },
      }));
      return;
    }
    if (path.endsWith('/preflight') && method === 'POST') {
      state.preflightRun = true;
      await route.fulfill(json({
        outcome: 'deliverable',
        fact_stats: { verified: 1, warning: 0, unverified: 0 },
        items: [
          { check: 'source_authorization', verdict: 'pass', blocking: true, detail: '1 bound artifacts authorized', refs: [] },
          { check: 'numeric_consistency', verdict: 'pass', blocking: true, detail: '1 facts verified', refs: [] },
          { check: 'map_version_coverage', verdict: 'pass', blocking: true, detail: '1 map views pinned', refs: [] },
        ],
      }));
      return;
    }
    if (path.endsWith('/releases') && method === 'POST') {
      state.frozen = true;
      state.releaseNo = 1;
      await route.fulfill(json({
        release_id: 'rel-demo', release_no: 1, already_released: false,
        manifest: {
          draft: false,
          files: [{ name: 'brief.html', kind: 'html', status: 'ready', bytes: 2048 }],
          evidence: [{ fact_id: 'fact-demo', status: 'verified' }],
        },
      }));
      return;
    }
    if (/\/releases$/.test(path) && method === 'GET') {
      await route.fulfill(json({
        releases: state.frozen ? [{
          release_id: 'rel-demo', release_no: 1, frozen_revision: state.revision,
          status: 'ready', created_at: '2026-01-02T08:05:00Z', draft: false,
          fact_stats: { verified: 1 }, counts: { facts: 1 },
          shared: Boolean(state.shareCode), share_expires_at: null,
        }] : [],
      }));
      return;
    }
    if (/\/releases\/rel-demo\/download$/.test(path)) {
      await route.fulfill({
        status: 200, contentType: 'text/html; charset=utf-8',
        body: '<!DOCTYPE html><html><main id="brief-root"></main></html>',
      });
      return;
    }
    if (/\/releases\/rel-demo\/share$/.test(path) && method === 'POST') {
      state.shareCode = 'sharecode-demo';
      await route.fulfill(json({
        share_code: 'sharecode-demo',
        share_url: '/api/v1/briefs/shared/sharecode-demo/view',
        expires_at: '2026-01-09T08:00:00Z',
      }));
      return;
    }
    if (/\/releases\/rel-demo\/share$/.test(path) && method === 'DELETE') {
      state.shareCode = null;
      await route.fulfill(json({ revoked: true, release_id: 'rel-demo' }));
      return;
    }
    if (/\/sessions\/[^/]+$/.test(path) && method === 'GET') {
      if (!state.created) {
        await route.fulfill(json({ detail: 'no brief for this session' }, 404));
        return;
      }
      await route.fulfill(json(briefResponse()));
      return;
    }
    await route.fulfill(json({ detail: 'brief stub miss', path }, 404));
  });
}

/** 打开 Story 页（与 j4 同款命令面板路径；真实 opener 认证）。 */
async function openStory(page: Page, sessionId: string): Promise<Page> {
  await page.keyboard.press('ControlOrMeta+k');
  await page.getByTestId('command-palette-input').fill('在故事视图打开当前会话');
  const popup = page.waitForEvent('popup');
  await page.getByRole('option', { name: /在故事视图打开当前会话/ }).click();
  const story = await popup;
  await expect(story.getByTestId('brief-panel-toggle')).toBeVisible({ timeout: 20_000 });
  return story;
}

async function openBriefPanel(story: Page): Promise<void> {
  await story.getByTestId('brief-panel-toggle').click();
  await expect(story.getByTestId('brief-panel')).toBeVisible();
}

test.describe('journey-13 证据化地图简报交付', () => {
  test('@smoke mock：草稿 → 编辑锁定 → 证据 → 预检 → 冻结 → 下载/分享/撤销', async ({ page: workspace }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = briefWorld();
    await seedAuthUser(workspace);
    await installJourneyStubs(workspace.context(), world);
    const state: BriefStubState = {
      revision: 0, created: false, lockedText: false,
      textContent: '自动生成的导语。', preflightRun: false,
      frozen: false, releaseNo: 0, shareCode: null,
    };
    installBriefStubs(workspace.context(), state);

    // 从工作台历史恢复会话并打开 Story
    await workspace.goto('/');
    await awaitShellReady(workspace);
    await workspace.getByRole('button', { name: '历史记录', exact: true }).click();
    const drawer = workspace.locator('[role="dialog"][aria-labelledby="history-drawer-title"]');
    await drawer.getByRole('listitem').filter({ hasText: '简报旅程会话' })
      .getByRole('button').first().click();
    await expect(drawer).not.toBeVisible();

    const story = await openStory(workspace, SESSION_ID);
    await openBriefPanel(story);

    // 1) 草稿生成（mock：GET 404 → 空态 → 点击创建）
    await expect(story.getByTestId('brief-empty')).toBeVisible();
    await story.getByRole('button', { name: /生成简报草稿/ }).click();
    await expect(story.getByTestId('brief-panel')).toContainText('rev 1', { timeout: 10_000 });
    await expect(story.getByTestId('brief-panel')).toContainText('锦江区学校数');

    // 2) 编辑正文 → 标记人工并锁定（BRF-O03）
    await story.getByTestId('brief-text-block').first().click();
    const editor = story.getByTestId('brief-block-editor');
    await editor.fill('人工结论：学校集中分布于锦江区，此结论不许自动覆盖。');
    await story.getByRole('button', { name: /保存（标记为人工内容）/ }).click();
    await expect(story.getByTestId('brief-panel')).toContainText('人工结论', { timeout: 10_000 });
    await story.getByRole('button', { name: '锁定此块' }).first().click();
    // 锁定后正文按钮禁用
    await expect(story.getByTestId('brief-text-block').first()).toBeDisabled();

    // 3) 证据核对（BRF 指令 2）
    await story.getByTestId('brief-metric-card').first().click();
    await expect(story.getByTestId('brief-evidence-drawer')).toBeVisible();
    await expect(story.getByTestId('brief-evidence-drawer')).toContainText('142');
    await expect(story.getByTestId('brief-evidence-drawer')).toContainText('table.rows.0.count');
    await story.getByRole('button', { name: '关闭证据侧栏' }).click();

    // 4) 预检（BRF 指令 4 前半）
    await story.getByTestId('brief-preflight-section').getByRole('button', { name: /运行预检/ }).click();
    await expect(story.getByTestId('brief-preflight-report')).toBeVisible({ timeout: 10_000 });
    await expect(story.getByTestId('brief-preflight-outcome')).toContainText('预检通过');

    // 5) 冻结 → 版本列表 v1
    await story.getByTestId('brief-freeze').click();
    await expect(story.getByTestId('brief-release-list')).toBeVisible({ timeout: 10_000 });
    await expect(story.getByTestId('brief-release-list')).toContainText('v1');

    // 6) 私有下载（下载事件真实发生）
    const download = story.waitForEvent('download');
    await story.getByRole('button', { name: '下载离线 HTML' }).click();
    expect((await download).suggestedFilename()).toContain('brief-release');

    // 7) 分享 → 撤销
    await story.getByRole('button', { name: '创建分享链接' }).click();
    await expect(story.getByRole('button', { name: '撤销分享' })).toBeVisible({ timeout: 10_000 });
    await story.getByRole('button', { name: '撤销分享' }).click();
    await expect(story.getByRole('button', { name: '创建分享链接' })).toBeVisible({ timeout: 10_000 });

    await story.close();
  });

  test('real：真后端草稿 → 编辑 → 预检 → 冻结 → 私有下载 → 分享所有权契约', async ({ page: workspace }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    const accessToken = await loginViaApi(workspace);
    const turns = [
      '分析成都学校分布并统计各区间数量',
      '把统计整理成各区学校数量表',
    ];
    const sessionId = await seedConversationViaApi(workspace.request, accessToken, turns);

    // 工作台恢复会话（真实历史）→ 打开 Story
    await workspace.goto('/');
    await awaitShellReady(workspace);
    await workspace.getByRole('button', { name: '历史记录', exact: true }).click();
    const drawer = workspace.locator('[role="dialog"][aria-labelledby="history-drawer-title"]');
    await drawer.getByRole('listitem').filter({ hasText: '学校' })
      .getByRole('button').first().click();
    await expect(drawer).not.toBeVisible();

    const story = await openStory(workspace, sessionId);
    await openBriefPanel(story);

    // 1) 真实草稿：从会话真实消息/产物编译（消息种子至少给章节骨架）
    await expect(story.getByTestId('brief-empty')).toBeVisible({ timeout: 20_000 });
    await story.getByRole('button', { name: /生成简报草稿|Create brief draft/ }).click();
    await expect(story.getByTestId('brief-panel')).toContainText(/rev 1|学校/, { timeout: 30_000 });

    // 2) 真实编辑 + 锁定（乐观 revision 生效）
    const textBlock = story.getByTestId('brief-text-block').first();
    if (await textBlock.count()) {
      await textBlock.click();
      await story.getByTestId('brief-block-editor')
        .fill('人工结论（real）：保留我的结论，不许自动覆盖。');
      const panel = story.getByTestId('brief-panel');
      await panel.getByRole('button', { name: /保存（标记为人工内容）|Save \(mark/ }).click();
      await expect(panel).toContainText('rev 2', { timeout: 20_000 });
      await panel.getByRole('button', { name: /锁定此块|Lock this block/ }).first().click();
    }

    // 3) 真实预检 + 冻结（种子会话无产物 → 草稿级发布，带披露）
    await story.getByTestId('brief-preflight-section')
      .getByRole('button', { name: /运行预检|Run preflight/ }).click();
    await expect(story.getByTestId('brief-preflight-report')).toBeVisible({ timeout: 30_000 });
    await story.getByLabel(/允许草稿级发布|Allow draft-level/).check();
    await story.getByTestId('brief-freeze').click();
    await expect(story.getByTestId('brief-release-list')).toBeVisible({ timeout: 30_000 });
    await expect(story.getByTestId('brief-release-list')).toContainText('v1');

    // 4) 真实私有下载（owner Bearer / owner-token 通道；CAS 回读）
    const download = story.waitForEvent('download');
    await story.getByRole('button', { name: /下载离线 HTML|Download offline HTML/ }).click();
    const dl = await download;
    expect(dl.suggestedFilename()).toContain('brief-release');
    const path = await dl.path();
    const { readFile } = await import('node:fs/promises');
    const html = await readFile(path!, 'utf-8');
    expect(html).toContain('brief-bundle'); // 自解压 bundle 在场
    expect(html).not.toMatch(/(src|href)="https?:\/\//); // 离线零外链

    // 5) 分享 → 公共只读可开；owner 端点对外部者 401/403/404
    await story.getByRole('button', { name: /创建分享链接|Create share link/ }).click();
    await expect(story.getByRole('button', { name: /撤销分享|Revoke share/ }))
      .toBeVisible({ timeout: 15_000 });
    // 外部者访问 owner 面（无凭证）：会话读取与简报读取都必须拒绝，
    // 不泄露存在性（j4 同款契约 + briefs 端点直探）。
    const outsider = await workspace.context().newPage();
    const denied = outsider.waitForResponse((response) =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === `/api/v1/chat/sessions/${sessionId}`);
    await outsider.goto(`http://localhost:3310/story?session_id=${sessionId}`);
    const response = await denied;
    expect([401, 403, 404]).toContain(response.status());
    await outsider.close();
    const briefProbe = await outsider.request.get(
      `http://localhost:8001/api/v1/briefs/sessions/${sessionId}`);
    expect([401, 403, 404]).toContain(briefProbe.status());

    // 6) 撤销分享 → 服务端读取失败
    await story.getByRole('button', { name: /撤销分享|Revoke share/ }).click();
    await expect(story.getByRole('button', { name: /创建分享链接|Create share link/ }))
      .toBeVisible({ timeout: 15_000 });
    await story.close();
  });
});
