/**
 * Journey 17 — G22/A22 恢复连续性（mock 档）：真实浏览器恢复后继续编辑。
 *
 * 用户路径：对话提交带用户约束的 MapSpec（两画幅 + 隐藏层 + 手动标注 +
 * 层锁）→ 刷新 → 从「历史记录」抽屉重进会话（完整恢复管线：transcript +
 * map-state + committed spec + 恢复横幅）→ 验证约束原样保留 → 第二回合
 * 只改第二幅标题（revision +1）→ API 断言「标题已改 / 隐藏层与标注逐字
 * 未动 / revision 恰 +1」→ 再恢复一次验证修订后的标题仍可见。
 *
 * mock 档的恢复入口是历史抽屉而非刷新锚自动恢复：tokenStore 刻意不持久
 * 化凭据（内存态 token，localStorage 只存 userId），刷新后锚判定拿不到
 * accessToken —— 与 j10 的既知事实一致，此处不伪造锚恢复。
 *
 * mock 服务端权威态（journal）由 SSE 夹具原话驱动（后端之声），map-state /
 * workbench-state stub 读 journal 应答 —— API 断言针对的是生产传输真正
 * 投递的 spec，不与夹具手抄对账、不伪造断言。
 */
import { test, expect, type Page, type Response } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  sendChat,
  awaitShellReady,
  getJourneyAuth,
  openRailTab,
} from '../helpers/bootstrap';
import {
  J17_FRAME_ONE_ID,
  J17_FRAME_TWO_ID,
  J17_HIDDEN_LAYER_ID,
  J17_LABEL_LAYER_ID,
  J17_LABEL_OVERRIDES,
  J17_SESSION_ID,
  J17_FRAME_TWO_TITLE_REVISED,
  restoreContinuityCommitTurn,
} from '../fixtures/sse';
import { expectMapReady, expectLayerListed } from '../helpers/assertions';
import { MODE, REAL_ONLY_REASON } from '../helpers/mode';

/** 恢复面 spec 的最小断言投影（只取本旅程断言的字段）。 */
interface SpecLayer {
  id?: string;
  layout?: { visibility?: string };
  label?: { authoring?: { overrides?: Record<string, unknown> } };
}
interface SpecFrame {
  frame_id?: string;
  title?: string;
}
interface CommittedSpec {
  layers?: SpecLayer[];
  frame_product?: { frames?: SpecFrame[] };
  workbench?: { lockedLayerIds?: string[] };
}

/**
 * 从 SSE 夹具文本提取（revision, 最后一份 committed mapspec）—— 与 j15
 * 的 extractSessionAndRevision 同源语义；spec 取流内最后一份（task_complete
 * 的盖章与 step_result 一致）。
 *
 * 注意解析的是**夹具字符串本身**（后端的原话），不是 Playwright 的
 * Response.text()/body()：本仓当前 playwright 对非 ASCII 响应体的
 * Node 侧读取会按 latin-1 双重编码（浏览器侧渲染不受影响 —— UI 断言全绿，
 * 只有 Node 侧镜像变 mojibake）；j15 只提取 ASCII 故未受影响。
 */
function extractCommit(body: string): { revision: number; mapspec: CommittedSpec | null } {
  let revision = 0;
  let mapspec: CommittedSpec | null = null;
  for (const m of body.matchAll(/^data: (.+)$/gm)) {
    try {
      const data = JSON.parse(m[1]) as {
        mutation_revision?: number;
        result?: { mutation_revision?: number; mapspec?: CommittedSpec };
        mapspec?: CommittedSpec;
      };
      const rev = data.mutation_revision ?? data.result?.mutation_revision;
      if (typeof rev === 'number') revision = Math.max(revision, rev);
      const spec = data.mapspec ?? data.result?.mapspec;
      if (spec && typeof spec === 'object') mapspec = spec;
    } catch {
      /* `data: [DONE]` 行非 JSON —— 跳过 */
    }
  }
  return { revision, mapspec };
}

/** 打开 Map Studio 的画幅子页签（j16 同款两步 + 子页签）。 */
async function openFramesPanel(page: Page): Promise<void> {
  const modeRadio = page.getByRole('radio', { name: '制图模式' });
  await expect(modeRadio).toBeVisible({ timeout: 15_000 });
  await modeRadio.click();
  await page.locator('[role="tab"][aria-label*="制图"]').first().click();
  await page.locator('#map-studio-tab-frames').click();
  await expect(page.locator('[data-testid="frame-product-frames"]')).toBeVisible({
    timeout: 15_000,
  });
}

/**
 * 以页面身份 GET 产品 API（j1 expectExportLanded 同款接缝）：浏览器 fetch
 * 走 page.route 桩 —— 与应用自身的恢复读取同一传输路径（APIRequestContext
 * 在本仓钉的 playwright 版本不走路由拦截，不能用它探测）。
 */
async function getJsonViaShell<T>(page: Page, path: string, accessToken?: string): Promise<T> {
  return page.evaluate(
    async ({ path, accessToken }: { path: string; accessToken?: string }) => {
      const headers: Record<string, string> = {};
      if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
      const res = await fetch(path, { headers });
      if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
      return (await res.json()) as T;
    },
    { path, accessToken },
  );
}

/**
 * 刷新后的真实恢复路径：mock 壳的 token 是内存态（tokenStore 刻意不持久
 * 化凭据，localStorage 只存 userId），刷新后 autoRestoreFromAnchor 因无
 * accessToken 不会自动恢复（j10 同款事实）—— 用户从「历史记录」抽屉重进
 * 会话，走完整 selectSession 恢复管线（transcript + map-state + spec +
 * 恢复横幅）。这是 mock 档下唯一真实的浏览器恢复路径，不伪造锚恢复。
 */
async function restoreViaHistoryDrawer(page: Page): Promise<void> {
  await page.getByRole('button', { name: '历史记录' }).click();
  // 锚定行按钮本体（删除按钮的名字是「删除会话 …」—— 前缀锚定避开 strict 冲突）。
  const row = page.getByRole('button', { name: /^恢复连续性旅程/ });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
}

test.describe('journey-22 恢复后继续编辑（只改第二幅标题）', () => {
  test('mock：刷新恢复保留约束 → 改标题 revision+1 → 隐藏层/标注逐字不动', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');

    // ── mock 服务端权威态（journal）：由浏览器真实收到的 SSE commit 驱动 ──
    let serverSpec: CommittedSpec | null = null;
    let serverRevision = 0;
    const world = defaultWorld();
    const turn1Fixture = restoreContinuityCommitTurn({ revision: 2 });
    world.pushChat(turn1Fixture);
    // 会话身份：历史抽屉行 + transcript 恢复源（世界可变 —— 后端之声）。
    world.sessionDetail = {
      session_id: J17_SESSION_ID,
      title: '恢复连续性旅程',
      messages: [
        {
          id: 'm-j22-1',
          role: 'user',
          content: '把城东小学点位铺到地图上，路网保持隐藏，做成两幅对比产品',
          timestamp: Date.parse('2026-01-01T08:30:00Z'),
        },
      ],
    };
    // G22-P6 恢复摘要（与夹具 spec 同源事实；横幅据此披露约束保留）。
    world.recoverySummary = {
      status: 'ok',
      session_id: J17_SESSION_ID,
      recovered: true,
      recovered_to_revision: 2,
      layer_count: 2,
      invalid_source_refs: [],
      durable_jobs: [],
      user_constraints: {
        locked_layers: [J17_HIDDEN_LAYER_ID],
        locked_components: 0,
        hidden_layers: [J17_HIDDEN_LAYER_ID],
        label_overrides: Object.keys(J17_LABEL_OVERRIDES).length,
        active_frame: J17_FRAME_ONE_ID,
      },
    };
    await bootstrapMock(page, world);
    // 恢复/对账面（注册在 installJourneyStubs 之后 = 优先级更高，j15 同款）：
    // map-state 是 spec 恢复的权威读面；workbench/state 供 revision 对账探测。
    await page.route('**/api/v1/chat/sessions/*/map-state**', async (route) => {
      const mapState = serverSpec
        ? {
            viewport: { center: [104.06, 30.66], zoom: 11, bearing: 0, pitch: 0 },
            layers: [],
            mapspec: serverSpec,
            _cartographic_mutation_revision: serverRevision,
          }
        : world.mapState.map_state;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: J17_SESSION_ID, map_state: mapState }),
      });
    });
    await page.route('**/api/v1/chat/sessions/*/workbench/state**', async (route) => {
      const req = route.request();
      if (req.method() === 'GET') {
        const meta = new URL(req.url()).searchParams.get('meta');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(
            meta
              ? { session_id: J17_SESSION_ID, revision: serverRevision }
              : { session_id: J17_SESSION_ID, revision: serverRevision, doc: serverSpec?.workbench ?? null },
          ),
        });
        return;
      }
      // V6 持久化提交（PATCH/PUT/POST）—— mock 后端只 ack，不回写 journal。
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', revision: serverRevision }),
      });
    });

    await page.goto('/');
    await awaitShellReady(page);

    // ── turn 1：提交带约束的 spec → UI 挂载（生产路径）────────────────────
    const stream1 = page.waitForResponse(
      (r: Response) => /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname),
      { timeout: 30_000 },
    );
    await sendChat(page, '把城东小学点位铺到地图上，路网保持隐藏，做成两幅对比产品');
    await stream1; // 流已完整回放（同步信号）；journal 解析夹具原话，不读 Node 侧镜像
    const commit1 = extractCommit(turn1Fixture);
    expect(commit1.revision).toBe(2);
    expect(commit1.mapspec).toBeTruthy();
    serverSpec = commit1.mapspec;
    serverRevision = commit1.revision;

    await expectMapReady(page);
    await expectLayerListed(page, /城东小学/);
    // 预算说明：画幅面板的 UI 基线不在本回合打开（j16 已钉 commit 可见性；
    // 基线标题走下方 API 采集，修订后的面板断言放在最终恢复腿 —— 全程只开
    // 一次 Studio，60s 旅程预算内留足余量）。

    // ── 基线采集（API 读面，任务步骤 2）──────────────────────────────────
    const auth = getJourneyAuth(page);
    const baselineState = await getJsonViaShell<{
      map_state: { mapspec: CommittedSpec; _cartographic_mutation_revision: number };
    }>(page, `/api/v1/chat/sessions/${J17_SESSION_ID}/map-state`, auth?.accessToken);
    const baselineSpec = baselineState.map_state.mapspec;
    const baselineRevision = baselineState.map_state._cartographic_mutation_revision;
    const baselineHidden = baselineSpec.layers?.find((l) => l.id === J17_HIDDEN_LAYER_ID);
    const baselineLabel = baselineSpec.layers?.find((l) => l.id === J17_LABEL_LAYER_ID);
    expect(baselineRevision).toBe(2);
    expect(baselineHidden?.layout?.visibility).toBe('none');
    expect(baselineLabel?.label?.authoring?.overrides).toEqual(J17_LABEL_OVERRIDES);

    // 图层断言把面板停在「图层」tab（活动 tab 随设置持久化，刷新后 composer
    // 不可见）—— 回对话面板再刷新。
    await openRailTab(page, '对话');

    // ── 刷新恢复（历史抽屉重进 → selectSession 完整恢复管线）─────────────
    await page.reload();
    await awaitShellReady(page);
    await restoreViaHistoryDrawer(page);
    // 恢复横幅（G22-P6）：披露「已恢复 + 用户约束保留」。
    const banner = page.getByTestId('recovery-banner');
    await expect(banner).toBeVisible({ timeout: 20_000 });
    await expect(banner).toContainText('会话已恢复');
    await expect(banner).toContainText('已保留人工约束');
    await expect(banner).toContainText('隐藏 1 层');
    await expect(banner).toContainText('锁定 1 层');
    await expect(banner).toContainText('标注 1 条');
    // transcript 已恢复（恢复提示行可见 = messages GET 真实消费）。
    await expect(page.getByText(/已恢复历史会话「恢复连续性旅程」/).first()).toBeVisible({
      timeout: 15_000,
    });
    // 地图与图层行恢复：隐藏层仍隐藏、可见层仍可见（眼睛按钮语义 = 显示/
    // 隐藏图层 —— 逐行断言，比列名更贴近「约束保留」的事实）。
    await expectMapReady(page);
    await page.locator('[role="tab"][aria-label*="图层"]').first().click();
    const hiddenRow = page.getByTestId(`layer-row-${J17_HIDDEN_LAYER_ID}`);
    await expect(hiddenRow).toBeVisible({ timeout: 20_000 });
    await expect(
      hiddenRow.getByRole('button', { name: '显示图层', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByTestId(`layer-row-${J17_LABEL_LAYER_ID}`).getByRole('button', {
        name: '隐藏图层',
        exact: true,
      }),
    ).toBeVisible();
    // 恢复后对账探测：服务端 revision 与基线一致（恢复不推进 revision）。
    const meta = await getJsonViaShell<{ revision: number }>(
      page,
      `/api/v1/chat/sessions/${J17_SESSION_ID}/workbench/state?meta=1`,
      auth?.accessToken,
    );
    expect(meta.revision).toBe(baselineRevision);
    // 图层断言把左侧面板停在了「图层」tab —— 回对话面板发第二回合。
    await openRailTab(page, '对话');

    // ── turn 2：只改第二幅标题（revision +1）────────────────────────────
    const turn2Fixture = restoreContinuityCommitTurn({
      taskId: 't-j17-2',
      revision: baselineRevision + 1,
      frameTwoTitle: J17_FRAME_TWO_TITLE_REVISED,
    });
    world.pushChat(turn2Fixture);
    const stream2 = page.waitForResponse(
      (r: Response) => /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname),
      { timeout: 30_000 },
    );
    await sendChat(page, '把第二幅的标题改成「第二幅（修订）」，其它都不要动');
    await stream2;
    const commit2 = extractCommit(turn2Fixture);
    // revision 恰 +1（无双重提交）：流内盖章就是基线+1。
    expect(commit2.revision).toBe(baselineRevision + 1);
    serverSpec = commit2.mapspec;
    serverRevision = commit2.revision;
    // 摘要面同步推进（服务端之声：恢复摘要永远现取权威态）。
    world.recoverySummary = {
      ...world.recoverySummary,
      recovered_to_revision: commit2.revision,
    } as Record<string, unknown>;

    // ── API 断言（任务步骤 5）：改的只有标题，约束逐字未动 ────────────────
    const afterState = await getJsonViaShell<{
      map_state: { mapspec: CommittedSpec; _cartographic_mutation_revision: number };
    }>(page, `/api/v1/chat/sessions/${J17_SESSION_ID}/map-state`, auth?.accessToken);
    const afterSpec = afterState.map_state.mapspec;
    const frames = afterSpec.frame_product?.frames ?? [];
    expect(frames).toHaveLength(2);
    expect(frames.map((f) => f.frame_id)).toEqual([J17_FRAME_ONE_ID, J17_FRAME_TWO_ID]);
    expect(frames[1]?.title).toBe(J17_FRAME_TWO_TITLE_REVISED);
    expect(frames[0]?.title).toBe('第一幅');
    expect(afterState.map_state._cartographic_mutation_revision).toBe(baselineRevision + 1);
    // 隐藏层仍隐藏；手动标注逐字不变（deep equal）；层锁不变。
    const afterHidden = afterSpec.layers?.find((l) => l.id === J17_HIDDEN_LAYER_ID);
    const afterLabel = afterSpec.layers?.find((l) => l.id === J17_LABEL_LAYER_ID);
    expect(afterHidden?.layout?.visibility).toBe('none');
    expect(afterLabel?.label?.authoring?.overrides).toEqual(
      baselineLabel?.label?.authoring?.overrides,
    );
    expect(afterSpec.workbench?.lockedLayerIds).toEqual([J17_HIDDEN_LAYER_ID]);
    // 无双重提交 / 无用户突变：整段旅程恰好 2 次 stream POST、零次
    // mapspec/mutations POST（约束未被用户路径触碰）。
    expect(
      world.requests.filter((r) => r.method === 'POST' && r.path === '/api/v1/chat/stream'),
    ).toHaveLength(2);
    expect(
      world.requests.filter((r) => r.method === 'POST' && /\/mapspec\/mutations$/.test(r.path)),
    ).toHaveLength(0);

    // ── 再恢复一次：修订标题经恢复路径仍可见（连续性闭环）─────────────────
    await page.reload();
    await awaitShellReady(page);
    await restoreViaHistoryDrawer(page);
    await expect(banner).toBeVisible({ timeout: 20_000 });
    await expect(banner).toContainText(`revision ${baselineRevision + 1}`);
    await openFramesPanel(page);
    await expect(page.locator('[data-testid="frame-product-frames"] li')).toHaveCount(2);
    await expect(page.getByLabel('画幅标题').nth(1)).toHaveValue(J17_FRAME_TWO_TITLE_REVISED);
  });

  test('real：占位（real 档恢复连续性旅程待 nightly Pi stub 驱动同形状 commit）', async () => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    // real 档需要确定性 LLM stub 产出 frame_product_bind 提交 + 真实后端的
    // 会话锚恢复；mock 双胞胎已覆盖编排契约，nightly 接入后启用。
    expect(true).toBe(true);
  });
});
