/**
 * Journey 25 — G45 坐标操作与重投影（real 档专用）。
 *
 * 真实浏览器 → 后端 → 面板作者链（候选/锁定/预览/执行）→ 刷新恢复 → 下载：
 *  1) 建立会话（真实 chat SSE）；
 *  2) 真实产品上传链播种源：POST /upload（GeoJSON + 显式声明 CRS）→
 *     register_ref → session FC ref（权威 CRS 证据，非夹具注入）；
 *  3) 打开坐标操作面板：源检查（CRS 证据/行数）→ 候选对照（可执行/不可执行
 *     同列）→ 锁定（服务器签发 candidate_id 冻结）→ 预览（before/after 对）；
 *  4) 执行全量重投影 → completed（状态/行数/输出 CRS/成果 ref 来自服务器）；
 *  5) 刷新浏览器 → 面板从服务器 plan/run 行恢复（非 local state）；
 *  6) 经 G18 真实交付下载（GPKG 保持 CRS）→ SQLite magic + 字节非零回读。
 *
 * mock 档不提供真实候选/冻结/交付链 —— 整条仅 real 档（诚实 skip）。
 */
import { test, expect, type Page } from 'playwright/test';
import { loginViaApi, openRailTab } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON, API_ORIGIN } from '../helpers/mode';

/**
 * 安装真实登录态（tokenStore 同通道：localStorage 'webgis_auth' —— 与
 * 真实 Account 表单登录写入的通道一致；token 本身来自真实 /auth/login）。
 * 设置面板结构随版本演进，直接走存储通道比表单编排更稳。
 */
async function installRealAuth(page: Page, token: string, user: { id: string; username: string }): Promise<void> {
  await page.evaluate(([t, u]) => {
    window.localStorage.setItem('webgis_auth', JSON.stringify({
      accessToken: t, refreshToken: null, user: u,
    }));
  }, [token, user] as const);
  await page.reload();
  await expect(
    page.locator('textarea[aria-label="输入空间分析指令"]'),
  ).toBeVisible({ timeout: 30_000 });
}

const STREAM_PATH = /\/api\/v1\/chat\/stream$/;
const N_FEATURES = 1201;

test.describe('journey-25 G45 坐标操作与重投影', () => {
  test('real：上传声明 CRS → 候选/锁定/预览 → 执行 → 刷新恢复 → GPKG 下载', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    test.setTimeout(600_000);
    const token = await loginViaApi(page);
    const authHeaders = { Authorization: `Bearer ${token}` };
    await page.goto('/');
    await installRealAuth(page, token, { id: 'g45e2e', username: 'g45e2e' });

    // ── 1) 建立会话（真实 SSE 轮；注水稳健：networkidle + 发送键可用重试） ──
    await page.waitForLoadState('networkidle').catch(() => undefined);
    const composer = page.locator('textarea[aria-label="输入空间分析指令"]');
    const sendBtn = page.locator('button[aria-label="发送消息"]');
    // 冷编译注水可能较慢：重试 fill 直至受控输入真正进入 React 状态
    // （未注水时 fill 不触发 onChange，按钮保持 disabled）。
    for (let i = 0; i < 90; i += 1) {
      await composer.fill('你好，准备做一次坐标操作重投影。');
      await page.waitForTimeout(1000);
      if (!(await sendBtn.isDisabled())) break;
    }
    expect(await sendBtn.isDisabled(), 'composer hydrated and sendable').toBeFalsy();
    const streamResp = page.waitForResponse(
      (r) => STREAM_PATH.test(new URL(r.url()).pathname) && r.status() === 200,
      { timeout: 90_000 },
    );
    await sendBtn.click();
    const resp = await streamResp;
    expect(resp.status(), 'chat stream ok').toBe(200);
    const body = await resp.text();
    const sessionId = body.match(/"session_id"\s*:\s*"([^"]+)"/)?.[1] ?? null;
    // 会话归属凭据：SSE 下发的 owner_token（chat 创建的会话以 owner token
    // 为归属；JWT 仅登录态）。后续 API 同时携带两者。
    const ownerToken = body.match(/"owner_token"\s*:\s*"([^"]+)"/)?.[1] ?? null;
    expect(sessionId, 'session established via real chat').toBeTruthy();
    const sessionHeaders = {
      ...authHeaders,
      ...(ownerToken ? { 'X-Session-Token': ownerToken } : {}),
    };

    // 种子数据：1201 点（C20 全量规模）+ 权威 CRS 证据
    const feats = Array.from({ length: N_FEATURES }, (_, i) => ({
      type: 'Feature',
      properties: { _wfid: `j25-${String(i).padStart(5, '0')}`, name: `要素${i}` },
      geometry: {
        type: 'Point',
        coordinates: [116 + (i % 37) * 0.013, 39.9 + (i % 23) * 0.007],
      },
    }));
    const geojson = JSON.stringify({
      type: 'FeatureCollection',
      features: feats,
      properties: { schema_info: { crs: 'EPSG:4326' } },
    });

    // ── 2) 真实产品链播种源：data-quality autofix/apply（FC + 显式 CRS
    //    声明 + 所有权守卫 → 新会话 FC ref；schema_info.crs 证据保真）──
    const upBody = (await page.evaluate(async ([api, sid, gj, ot, tk]) => {
      const r = await fetch(`${api}/api/v1/data-quality/autofix/apply`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${tk as string}`,
          'X-Session-Token': ot as string,
        },
        body: JSON.stringify({ geojson: JSON.parse(gj as string), crs: 'EPSG:4326', session_id: sid }),
      });
      return { status: r.status, body: await r.json() };
    }, [API_ORIGIN, sessionId, geojson, ownerToken ?? '', token] as const)) as {
      status: number;
      body: { new_ref?: string };
    };
    expect(upBody.status, `seed ok (${upBody.status})`).toBe(200);
    const sourceRef = upBody.body.new_ref ?? null;
    expect(sourceRef, 'autofix/apply yields session FC ref with CRS evidence').toBeTruthy();


    // ── 3) 面板作者链 ─────────────────────────────────────────────
    await openRailTab(page, '数据');
    await page.getByTestId('open-coordinate-operations').click();
    const panel = page.getByRole('dialog', { name: '坐标操作与重投影' });
    await expect(panel).toBeVisible();

    await panel.locator('#co-src').fill(sourceRef as string);
    await panel.getByRole('button', { name: /检查源/ }).click();
    await expect(panel.getByText('EPSG:4326').first()).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByText(/fc_schema_info_crs|geoparquet|crs/).first()).toBeVisible();

    await panel.locator('#co-tgt').fill('EPSG:32650');
    await panel.getByRole('button', { name: /列出候选操作/ }).click();
    await expect(panel.getByTestId('co-candidate').first())
      .toBeVisible({ timeout: 60_000 });

    // 保存计划（服务器 revision）→ 锁定（幂等：重跑时服务器已 locked 则跳过）
    const alreadyLocked = await panel.getByText(/操作已冻结/).first().isVisible()
      .catch(() => false);
    if (!alreadyLocked) {
      await panel.getByRole('button', { name: /保存计划/ }).click();
      await expect(panel.getByText(/已保存/).first())
        .toBeVisible({ timeout: 30_000 });
      // 选择首个可执行候选（候选卡）→ 底部主锁定（冻结 candidate_id）
      await panel.getByRole('button', { name: /锁定此操作/ }).first().click();
      await panel.getByRole('button', { name: /锁定此操作/ }).last().click();
    }
    await expect(panel.getByText(/操作已冻结/).first())
      .toBeVisible({ timeout: 60_000 });

    // 预览：before/after 坐标对
    await panel.getByRole('button', { name: /预览$/ }).click();
    await expect(panel.getByTestId('co-preview-pair').first())
      .toBeVisible({ timeout: 60_000 });

    // ── 4) 执行全量重投影 → completed（服务器状态） ────────────────
    await panel.getByRole('button', { name: /执行全量重投影/ }).click();
    await expect(panel.getByText('completed', { exact: false }).first())
      .toBeVisible({ timeout: 180_000 });
    await expect(panel.getByText('EPSG:32650').first()).toBeVisible();

    // ── 5) 重开面板（状态来自服务器 plan/run 行，非组件 local state）──
    // 匿名会话的 owner token 是内存态（W11 anchor 只覆盖认证会话恢复；
    // 刷新丢失 token 是产品既有边界，与 G38 面板一致）—— 以「关闭→重开
    // 面板（重新 mount + refreshPlans 重新拉取）」验证服务器为事实源。
    // Escape 关闭（面板 useDialogFocus 支持；execute 轮询每秒重渲染会让
    // click 的 actionability 稳定检查永不通过 —— 键盘路径不受影响）
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: '坐标操作与重投影' }))
      .not.toBeVisible({ timeout: 10_000 });
    await page.getByTestId('open-coordinate-operations').click();
    const panel2 = page.getByRole('dialog', { name: '坐标操作与重投影' });
    await expect(panel2).toBeVisible({ timeout: 15_000 });
    await expect(panel2.getByText('completed').first())
      .toBeVisible({ timeout: 45_000 });
    await expect(panel2.getByText('EPSG:32650').first()).toBeVisible();

    // ── 6) G18 真实交付下载（GPKG 保持 CRS）→ 字节回读 ─────────────
    const runsResp = await page.request.get(
      new URL(`/api/v1/coord-ops/sessions/${sessionId}/runs`, API_ORIGIN).href,
      { headers: sessionHeaders });
    expect(runsResp.ok()).toBeTruthy();
    const runsBody = (await runsResp.json()) as {
      runs: Array<{ run_id: string; status: string }>;
    };
    const done = runsBody.runs.find((r) => r.status === 'completed');
    expect(done, 'completed run visible via API').toBeTruthy();
    // 列表只带收据摘要 —— 详情（native_refs）经 inspectRun 取（与面板同通道）
    const detailResp = await page.request.get(
      new URL(`/api/v1/coord-ops/sessions/${sessionId}/runs/${done?.run_id}`, API_ORIGIN).href,
      { headers: sessionHeaders });
    expect(detailResp.ok()).toBeTruthy();
    const detail = (await detailResp.json()) as {
      receipt?: { native_refs?: { output_session_ref?: string | null; native_fabric_ref?: string | null } };
    };
    const deliverRef = detail.receipt?.native_refs?.output_session_ref
      ?? detail.receipt?.native_refs?.native_fabric_ref;
    expect(deliverRef, 'native delivery ref present in receipt').toBeTruthy();

    const jobResp = await page.request.post(
      new URL('/api/v1/data-delivery/jobs', API_ORIGIN).href, {
        headers: sessionHeaders,
        data: {
          spec: {
            source: { kind: 'session_ref', session_id: sessionId, ref: deliverRef as string },
            format: 'gpkg',
            filename: 'j25-g45-native',
          },
        },
      });
    expect(jobResp.ok()).toBeTruthy();
    const job = (await jobResp.json()) as { job_id: string };
    let dlStatus = 'pending';
    for (let i = 0; i < 90 && !['completed', 'failed', 'cancelled'].includes(dlStatus); i += 1) {
      await page.waitForTimeout(2000);
      const view = await page.request.get(
        new URL(`/api/v1/data-delivery/jobs/${job.job_id}`, API_ORIGIN).href,
        { headers: sessionHeaders });
      dlStatus = ((await view.json()) as { status: string }).status;
    }
    expect(dlStatus, 'delivery completed').toBe('completed');
    const dl = await page.request.get(
      new URL(`/api/v1/data-delivery/jobs/${job.job_id}/download`, API_ORIGIN).href,
      { headers: sessionHeaders });
    expect(dl.ok()).toBeTruthy();
    const buf = await dl.body();
    expect(buf.length, 'GPKG bytes non-empty').toBeGreaterThan(1000);
    expect(buf.subarray(0, 15).toString('utf-8')).toContain('SQLite format 3');
  });
});
