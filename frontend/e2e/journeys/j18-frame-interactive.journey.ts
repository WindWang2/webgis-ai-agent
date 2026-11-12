/**
 * Journey 18 — G25 多画幅真实交互运行时（mock 档 + real 档）。
 *
 * mock 档（真实浏览器 + 全 API 夹具）：三画幅产品恢复 → 打开网格 →
 * 活动画幅渲染真实 maplibre 地图（WebGL 画布）→ 活实例预算 ≤2（A09，
 * 页面真实 canvas 计数）→ 逐帧观察诚实分类（未挂载帧 not_observed）→
 * 激活第二幅（set_frame_product CAS + scope 徽标）→ 键盘 ']' 切换 →
 * Escape 关闭。
 *
 * real 档（真后端 :8001 + 确定性 LLM stub + 真浏览器）：stub 热点分析建
 * 真实图层 → 经真实 mutation API（set_layout frames + set_frame_product
 * config，CAS）绑定三画幅产品 → 网格三瓦片 + 激活第二幅 → 刷新后
 * active_frame 从服务端真相恢复（A20）。
 */
import { test, expect, type Page, type Response } from 'playwright/test';
import {
  bootstrapMock,
  defaultWorld,
  sendChat,
  awaitShellReady,
  loginViaApi,
  getJourneyAuth,
} from '../helpers/bootstrap';
import { sse } from '../fixtures/sse';
import { API_ORIGIN, MODE, REAL_ONLY_REASON } from '../helpers/mode';

/** mock 档三画幅产品 SSE 夹具 —— 内联真实要素（year×3 × 区县×3）。 */
function frameInteractiveTurn(opts: { sessionId?: string; revision?: number } = {}): string {
  const sessionId = opts.sessionId ?? 's-j17';
  const revision = opts.revision ?? 4;
  const legendSpec = {
    type: 'graduated', field: 'per_10k', breaks: [20, 26, 32, 38],
    palette: 'Blues',
    palette_colors: ['#c6dbef', '#6baed6', '#2171b5'],
    labels: ['20 – 26', '26 – 32', '32 – 38'],
  };
  const feats = [2020, 2022, 2024].flatMap((year) =>
    ['jinjiang', 'wuhou', 'gaoxin'].map((d, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [104.0 + i * 0.08, 30.6] },
      properties: { year, district: d, per_10k: 21 + i * 7 + (year - 2020) },
    })),
  );
  const frameSlot = (frameId: string, title: string, syncGroup: string) => ({
    frame_id: frameId, title, legend: 'shared', sync_group: syncGroup,
    data: {
      ref: 'ref:indicators', layer_id: 'lyr-districts', field: 'per_10k',
      content_revision: 5,
      time_window: {
        frame_id: frameId, label: title,
        start: `${title}-01-01T00:00:00Z`, end: `${Number(title) + 1}-01-01T00:00:00Z`,
        time_field: 'year', time_representation: 'year_int', granularity: 'year',
        timezone: 'UTC', layer_id: 'lyr-districts',
      },
    },
  });
  const mapspec = {
    version: '2.0',
    sources: {
      'src-districts': { type: 'geojson', inlineData: { type: 'FeatureCollection', features: feats } },
    },
    layers: [
      { id: 'lyr-districts', source: 'src-districts', type: 'circle', visible: true, legend_spec: legendSpec },
    ],
    layout: {
      frames: [
        { id: 'fp-t-2020', title: '2020' },
        { id: 'fp-t-2022', title: '2022' },
        { id: 'fp-t-2024', title: '2024' },
      ],
    },
    frame_product: {
      schema_version: 'frame_product.v1', enabled: true,
      product_kind: 'time_series',
      grid: { columns: 3, title: '成都每万人小学学位（交互）' },
      frames: [
        frameSlot('fp-t-2020', '2020', 'A'),
        frameSlot('fp-t-2022', '2022', 'A'),
        frameSlot('fp-t-2024', '2024', 'B'),
      ],
      shared_expression: {
        field: 'per_10k', semantics: 'other', method: 'equal_interval',
        k: 3, palette: 'Blues', breaks: [20, 26, 32, 38], domain_status: 'complete',
      },
      active_frame: 'fp-t-2020',
    },
  };
  const taskId = 't-j17';
  return [
    sse('session', { session_id: sessionId }),
    sse('task_start', { task_id: taskId, session_id: sessionId }),
    sse('token', { content: '已创建三幅可交互年度对比产品。' }),
    sse('step_result', {
      task_id: taskId,
      step_id: 'sr-j17',
      tool: 'frame_product_bind',
      mutation_revision: revision,
      mapspec,
      result: { success: true, status: 'ok', frames: 3, mapspec, mutation_revision: revision },
    }),
    sse('task_complete', { task_id: taskId, mutation_revision: revision, mapspec }),
    'data: [DONE]\n\n',
  ].join('');
}

interface ObsRow {
  frame_id: string;
  mode: string;
  camera?: { center: [number, number]; zoom: number } | null;
}

async function readObservation(page: Page): Promise<{ rows: ObsRow[]; liveCount: number; budget: number }> {
  return page.evaluate(() => {
    const obs = (window as unknown as {
      __g25FrameObservation?: () => { rows: ObsRow[]; liveCount: number; budget: number };
    }).__g25FrameObservation;
    if (!obs) return { rows: [], liveCount: -1, budget: -1 };
    const p = obs();
    return { rows: p.rows, liveCount: p.liveCount, budget: p.budget };
  });
}

test.describe('journey-18 多画幅交互运行时', () => {
  test('mock：网格三幅真实地图 · 激活CAS · 键盘 · 预算 · 诚实观察', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');
    const world = defaultWorld();
    world.pushChat(frameInteractiveTurn({ sessionId: 's-j17', revision: 4 }));
    await bootstrapMock(page, world);
    await page.route('**/api/v1/chat/sessions/*/workbench/state**', async (route) => {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ session_id: 's-j17', revision: 4 }),
      });
    });
    const sentMutations: Array<Record<string, unknown>> = [];
    await page.route('**/api/v1/chat/sessions/*/mapspec/mutations', async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      sentMutations.push(body);
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', mutation_revision: 5 }),
      });
    });

    await page.goto('/');
    await awaitShellReady(page);
    const streamWait = page.waitForResponse(
      (r: Response) => /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname),
      { timeout: 30_000 },
    );
    await sendChat(page, '把成都学位按 2020/2022/2024 做成三幅可交互对比');
    await streamWait;

    // ① 入口 chip（产品在场）。
    const chip = page.getByTestId('frame-grid-open-chip');
    await expect(chip).toBeVisible({ timeout: 20_000 });
    await chip.click();

    // ② 三瓦片，稳定 frame_id 身份（A01）。
    const tiles = page.getByTestId('frame-grid-tile');
    await expect(tiles).toHaveCount(3, { timeout: 20_000 });
    await expect(page.locator('[data-frame-id="fp-t-2020"]')).toBeVisible();
    await expect(page.locator('[data-frame-id="fp-t-2024"]')).toBeVisible();

    // ③ 活动画幅渲染真实 WebGL 地图（maplibre canvas 在瓦片内）。
    const activeTile = page.locator('[data-frame-id="fp-t-2020"]');
    await expect(
      activeTile.locator('canvas.maplibregl-canvas'),
    ).toBeVisible({ timeout: 30_000 });

    // ④ 活实例预算：页面 maplibre canvas 总数 ≤ 2（主图 + 活动画幅，A09）。
    await expect
      .poll(async () => page.locator('canvas.maplibregl-canvas').count(), { timeout: 20_000 })
      .toBeLessThanOrEqual(2);

    // ⑤ 逐帧观察：未挂载帧 not_observed（不冒充 live，A12/P6）。
    await expect
      .poll(async () => {
        const obs = await readObservation(page);
        return obs.rows.find((r) => r.frame_id === 'fp-t-2022')?.mode ?? '';
      }, { timeout: 20_000 })
      .toBe('not_observed');

    // ⑥ 激活第二幅：CAS mutation + scope 徽标（A02/A03 编辑面）。
    await page.locator('[data-frame-id="fp-t-2022"]').getByTestId('frame-grid-activate').click();
    await expect
      .poll(() => sentMutations.length, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);
    expect(sentMutations[0]?.intent).toBe('set_frame_product');
    expect((sentMutations[0]?.patch as { active_frame?: string })?.active_frame).toBe('fp-t-2022');
    await expect(page.getByTestId('frame-grid-scope-chip')).toContainText('fp-t-2022', {
      timeout: 20_000,
    });
    // 活动瓦片转移：fp-t-2022 现在承载真实地图画布。
    await expect(
      page.locator('[data-frame-id="fp-t-2022"]').locator('canvas.maplibregl-canvas'),
    ).toBeVisible({ timeout: 30_000 });

    // ⑦ 键盘 ']' 激活下一幅；'[' 回退（A25）。
    await page.keyboard.press(']');
    await expect(page.getByTestId('frame-grid-scope-chip')).toContainText('fp-t-2024', {
      timeout: 15_000,
    });
    await page.keyboard.press('[');
    await expect(page.getByTestId('frame-grid-scope-chip')).toContainText('fp-t-2022', {
      timeout: 15_000,
    });

    // ⑧ Escape 关闭 → 回到 chip（A25；关闭路径含 ≤2.5s 捕获，串行链上界放宽）。
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('frame-grid-root')).toBeHidden({ timeout: 20_000 });
    await expect(page.getByTestId('frame-grid-open-chip')).toBeVisible({ timeout: 10_000 });
  });

  /**
   * real：真实服务端半场端到端（P6 闭环 + 真实 mutation 管线 + 真实观察指纹门）。
   *
   * 平台边界（如实披露，非本方向越权代修）：
   * - 制图 MapSpec 目前仅经 live-turn SSE 到达浏览器；无 turn-less/reload
   *   恢复通道 → 「seed 后浏览器内网格交互」在真栈不可达（mock 档以同构
   *   SSE 夹具在真实浏览器覆盖该面）。
   * - LLM stub 的 call-count 脚本与 Pi-only 多轮调用存在 seam 失配
   *   （dispatch cache miss → tool_calls=0），工具回合不可用。
   * 故 real 档断言可真实验证的服务端闭环：真登录 → UI 真会话 →
   * runner-local 真实 mutation 管线种子（apply_gis_mutation 全意图）→
   * 真实 cartographic-observation POST（mapspec_fingerprint 指纹门 +
   * frames 增维）→ frame_product_status 终验回读逐帧行。
   */
  test('real：真实管线种子 → 观察指纹门接受 → status 逐帧终验（P6 闭环）', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);

    // ① UI 内真实对话建会话（stub 文本回合；会话/所有权为真）。
    const streamWait = page.waitForResponse(
      (r: Response) =>
        /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname) && r.status() === 200,
      { timeout: 45_000 },
    );
    await sendChat(page, '建立会话');
    const streamRes = await streamWait;
    const sidMatch = (await streamRes.text()).match(/"session_id"\s*:\s*"([^"]+)"/);
    expect(sidMatch?.[1]).toBeTruthy();
    const sessionId = sidMatch![1];
    const auth = getJourneyAuth(page);
    expect(auth?.user?.id).toBeTruthy();

    // ② runner-local 种子：真实 apply_gis_mutation 管线（UpsertSource/
    //    UpsertLayer/SetLayout/SetFrameProduct）—— 与 mapspec 路由/Pi 工具
    //    同一生产写路径；返回指纹供观察 POST 指纹门。
    const seeded = await runSeedScript(['--mode', 'seed'], sessionId, auth!.user!.id);
    expect(seeded.status).toBe('ok');
    expect(seeded.frame_ids).toHaveLength(3);
    expect(seeded.mutation_revision).toBeGreaterThan(0);
    expect(typeof seeded.mapspec_fingerprint).toBe('string');

    // ③ 真实 cartographic-observation POST（指纹门 + frames 增维；模拟
    //    浏览器观察上报的 wire 契约 —— 服务端按指纹盖章 revision）。
    const obsRes = await page.request.post(
      new URL(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/cartographic-observation`, API_ORIGIN).href,
      {
        headers: { Authorization: `Bearer ${auth!.accessToken}`, 'Content-Type': 'application/json' },
        data: {
          client_generation: 1,
          mapspec_fingerprint: seeded.mapspec_fingerprint,
          style_loaded: true,
          frames: [
            { frame_id: 'fp-r-2020', mode: 'live_observed', mapspec_revision: seeded.mutation_revision },
            { frame_id: 'fp-r-2022', mode: 'snapshot', mapspec_revision: seeded.mutation_revision, fingerprint: 'ab12cd34' },
            // 浏览器绝不冒充导出验证：export_verified 会被 DTO 拒绝（缺席语义）。
            { frame_id: 'fp-r-2024', mode: 'export_verified' },
          ],
        },
      },
    );
    expect(obsRes.ok(), `observation rejected: ${obsRes.status()} ${await obsRes.text()}`).toBeTruthy();
    // m5：路由对指纹门拒绝也回 200（observation_accepted=false）—— 必须断言接受语义。
    const obsBody = (await obsRes.json()) as {
      observation_accepted?: boolean;
      observation_rejection_reason?: string | null;
    };
    expect(
      obsBody.observation_accepted,
      `observation not accepted: ${obsBody.observation_rejection_reason}`,
    ).toBe(true);

    // ④ frame_product_status 终验回读：逐帧行吸收观察（A: live_observed、
    //    B: snapshot、C: export_verified 被拒 → not_observed）。
    const verified = await runSeedScript(['--mode', 'verify'], sessionId, auth!.user!.id);
    expect(verified.status).toBe('ok');
    const byId = new Map(
      (verified.frames as Array<{ frame_id: string; observation: string }>)
        .map((r) => [r.frame_id, r.observation]),
    );
    expect(byId.get('fp-r-2020')).toBe('live_observed');
    expect(byId.get('fp-r-2022')).toBe('snapshot');
    expect(byId.get('fp-r-2024')).toBe('not_observed'); // 词表外模式 → 证据缺席
    const summary = verified.verification_summary as Record<string, number>;
    expect(summary.live_observed).toBe(1);
    expect(summary.snapshot_only).toBe(1);
  });
});

/** runner-local 种子（real-jobs.ts 同款 execFile 纪律）。 */
async function runSeedScript(mode: string[], sessionId: string, userId: string): Promise<{
  status: string;
  frame_ids?: string[];
  layer_id?: string;
  mutation_revision?: number;
  mapspec_fingerprint?: string;
  verification_summary?: Record<string, number>;
  frames?: Array<{ frame_id: string; observation: string }>;
}> {
  const { execFile } = await import('node:child_process');
  const { promisify } = await import('node:util');
  const path = await import('node:path');
  const run = promisify(execFile);
  const repoRoot = path.resolve(__dirname, '../../..');
  const { stdout } = await run(
    process.env.E2E_PYTHON ?? 'python',
    ['-m', 'scripts.seed_journey_frames', ...mode, '--session-id', sessionId, '--user-id', userId],
    { cwd: repoRoot, timeout: 120_000 },
  );
  return JSON.parse(stdout.trim().split('\n').at(-1) ?? '{}');
}
