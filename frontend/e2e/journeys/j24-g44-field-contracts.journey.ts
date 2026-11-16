/**
 * Journey 24 — G44 业务字段合同与结构化录入（real 档专用）。
 *
 * 真实浏览器 → 真实后端 → 权威合同 DB / session store：
 *  1) 一轮对话建立会话（stub LLM 边界）；
 *  2) 经 owner-token API 种子：可编辑图层 → 2 要素 → 草稿合同 → 完整评估
 *     （零违反）→ owner 激活（F13/F14 的 UI 前置全部走真实 REST）；
 *  3) active 绑定经权威 REST 断言（浏览器发出的真实请求）；
 *  4) 刷新页面后绑定仍 ok（F35 权威面；表单渲染由组件测试覆盖）；
 *  5) owner API 直接提交越界值 → 同一 central validator 服务端拒绝（F26）。
 *
 * 工作台面板 UI 打开路径由组件测试（mock 档）覆盖；
 * 工作区壳层（侧栏收起/双实例 tab）real 档既有不稳定，不在本旅程断言。
 *
 * mock 档不提供 field-contracts 夹具 —— 整条仅 real 档（诚实 skip）。
 */
import { test, expect } from 'playwright/test';
import type { Response } from 'playwright/test';
import { loginViaApi, awaitShellReady, sendChat } from '../helpers/bootstrap';
import { MODE, REAL_ONLY_REASON, API_ORIGIN } from '../helpers/mode';

const STREAM_PATH = /\/api\/v1\/chat\/stream$/;

function streamResponse(page: import('playwright/test').Page, timeout = 90_000): Promise<Response> {
  return page.waitForResponse(
    (r) => STREAM_PATH.test(new URL(r.url()).pathname) && r.status() === 200,
    { timeout },
  );
}

interface Seeded {
  sessionId: string;
  bearer: string;
  refId: string;
  contractId: string;
  createdWfid: string;
}

async function seedWorld(page: import('playwright/test').Page, seeded: Seeded): Promise<void> {
  const base = `${API_ORIGIN}/api/v1/chat/sessions/${encodeURIComponent(seeded.sessionId)}`;
  const headers = { Authorization: `Bearer ${seeded.bearer}` };

  // 2 要素（合法数据：激活前置）
  const preview = await page.request.post(`${base}/feature-edits/preview`, {
    headers,
    data: {
      target: { ref_id: seeded.refId },
      ops: [{
        op: 'create_features',
        features: [
          { geometry: { type: 'Point', coordinates: [116.30, 39.90] },
            properties: { asset_no: 'A-001', status: 'planned', capacity: 12.5 } },
          { geometry: { type: 'Point', coordinates: [116.31, 39.91] },
            properties: { asset_no: 'A-002', status: 'active', capacity: 9 } },
        ],
      }],
    },
  });
  expect(preview.ok(), 'seed preview').toBeTruthy();
  const plan = (await preview.json()) as { plan_id: string };
  expect(plan.plan_id).toBeTruthy();
  const decide = await page.request.post(
    `${base}/feature-edits/plans/${plan.plan_id}/decision`,
    { headers, data: { action: 'approve' } },
  );
  expect(decide.ok(), 'seed commit').toBeTruthy();
  const receipt = (await decide.json()) as { receipt?: { created_wfids?: string[] } };
  seeded.createdWfid = receipt.receipt?.created_wfids?.[0] ?? '';

  // 草稿 → 完整评估（轮询至 completed）→ 激活
  const created = await page.request.post(`${base}/field-contracts`, {
    headers,
    data: {
      ref_id: seeded.refId,
      document: {
        version: 1,
        title: '旅程设施合同',
        fields: [
          { field: 'asset_no', type: 'string', required: true, min_length: 3, max_length: 16 },
          { field: 'status', type: 'string', enum: ['planned', 'active', 'retired'] },
          { field: 'capacity', type: 'number', min: 0 },
          { field: 'retired_reason', type: 'string', nullable: true },
        ],
        cross_rules: [
          { rule_id: 'rr', kind: 'conditional_required',
            when: { field: 'status', op: 'eq', value: 'retired' }, require: ['retired_reason'] },
          { rule_id: 'uniq', kind: 'unique', fields: ['asset_no'] },
        ],
      },
    },
  });
  expect(created.ok(), 'contract create').toBeTruthy();
  const contract = (await created.json()) as { contract_id: string };
  seeded.contractId = contract.contract_id;

  const assessed = await page.request.post(`${base}/field-contracts/${contract.contract_id}/assess`, {
    headers,
  });
  expect(assessed.ok(), 'assess start').toBeTruthy();
  const started = (await assessed.json()) as { assessment: { id: string } };
  let status = '';
  for (let i = 0; i < 120 && status !== 'completed'; i += 1) {
    await page.waitForTimeout(500);
    const got = await page.request.get(
      `${base}/field-contracts-assessments/${started.assessment.id}`, { headers });
    if (!got.ok()) break;
    status = ((await got.json()) as { status: string }).status;
  }
  expect(status, 'assessment completed').toBe('completed');

  const activated = await page.request.post(
    `${base}/field-contracts/${contract.contract_id}/activate`, { headers });
  expect(activated.ok(), 'activate').toBeTruthy();
}

test.describe('journey-24 G44 业务字段合同与结构化录入', () => {
  test('real：作者激活合同 → 面板绑定 → 结构化表单 → 刷新持久（浏览器全链）', async ({ page }) => {
    test.skip(MODE !== 'real', REAL_ONLY_REASON);
    test.setTimeout(300_000); // 冷 next dev 编译 + Pi 子进程链；旅程自有确定性等待
    const bearer = await loginViaApi(page);
    await page.goto('/');
    await awaitShellReady(page);

    // ── 轮 1：建立会话（stub LLM；捕获 sid 与 owner_token）─────────
    // 首轮可能撞上 UI 恢复/双发的 in-flight 守卫（503 Session busy）——
    // 与 Pi 旅程同款重试语义：有限次重发，任何终态失败都披露原始响应。
    let sid = '';
    let lastBody = '';
    for (let attempt = 0; attempt < 3 && !sid; attempt += 1) {
      const stream1 = streamResponse(page, 120_000);
      await sendChat(page, attempt === 0 ? 'g44-browser-warmup' : `g44-browser-warmup-${attempt}`);
      const res1 = await stream1;
      lastBody = await res1.text();
      sid = lastBody.match(/"session_id"\s*:\s*"([^"]+)"/)?.[1] ?? '';
      if (!sid) await page.waitForTimeout(5_000);
    }
    expect(sid, `chat stream produced no session id; last body: ${lastBody.slice(0, 400)}`).toBeTruthy();
    expect(lastBody).toContain('task_complete');

    // ── turn 完全落定：进行中的 turn 会让应用进入聚焦态（侧栏收起）。
    // task_complete 已在流内断言；再给 UI 一小段收敛窗口（不依赖具体
    // status 文案 —— 完成态文案随 locale/任务类型变化）。
    await page.waitForTimeout(2_500);

    // ── 种子：图层 + 要素 + 合同（真实 REST，Bearer 通道）─────────
    // 工作台面板的 UI 打开路径由组件测试（mock 档）覆盖；
    // 本旅程的权威链断言走同一 HTTP 面（浏览器上下文发出的真实请求）。
    // 图层：经 feature-edits datasets 创建（真实挂层 + ref）
    const provision = await page.request.post(
      `${API_ORIGIN}/api/v1/chat/sessions/${encodeURIComponent(sid)}/feature-edits/datasets`,
      { headers: { Authorization: `Bearer ${bearer}` },
        data: { name: 'g44-journey-facilities', geometry_type: 'Point' } },
    );
    expect(provision.ok(), 'dataset provision').toBeTruthy();
    const provisioned = (await provision.json()) as { ref_id: string };
    const seededRef = provisioned.ref_id;
      expect(seededRef).toBeTruthy();

    const seeded: Seeded = { sessionId: sid, bearer, refId: seededRef, contractId: '', createdWfid: '' };
    await seedWorld(page, seeded);

    // ── 绑定（权威 REST，浏览器上下文）：active 合同 + 要素数 ─────
    const base = `${API_ORIGIN}/api/v1/chat/sessions/${encodeURIComponent(sid)}`;
    const authHeaders = { Authorization: `Bearer ${bearer}` };
    const bindingResp = await page.request.get(
      `${base}/field-contracts/datasets/${encodeURIComponent(seededRef)}/binding`, { headers: authHeaders });
    expect(bindingResp.ok(), 'binding status via browser context').toBeTruthy();
    const bindingView = (await bindingResp.json()) as {
      status: string;
      contract: { title?: string; revision?: number } | null;
    };
    expect(bindingView.status).toBe('ok');
    expect(bindingView.contract?.title).toBe('旅程设施合同');

    // ── F35：刷新页面 → 图层/表单全部来自权威状态 ────────────────
    // （种子挂层发生在页面加载之后；reload 让前端以服务端权威状态重建，
    //  同时完成「重开后合同仍在」的旅程断言。）
    await page.reload();
    await awaitShellReady(page);
    const bindingAfter = await page.request.get(
      `${base}/field-contracts/datasets/${encodeURIComponent(seededRef)}/binding`, { headers: authHeaders });
    expect(bindingAfter.ok()).toBeTruthy();
    const bindingView2 = (await bindingAfter.json()) as { status: string };
    expect(bindingView2.status).toBe('ok');

    // ── 服务端门禁兜底：owner API 直接提交越界值被同一 validator 拒 ──
    //（结构化表单按合同渲染的断言由 feature-layer-editor.test.tsx 与
    // structured-feature-form.test.tsx 组件测试覆盖 —— 冷 dev server 下
    // 图层行水合时序不稳定，真实浏览器编辑腿不在本旅程断言，见
    // planning/G44-FIELD-CONTRACTS/TEST_MATRIX.md 的分层记录。）
    // REST preview 的 typed 拒绝面：200 + committable=false + contract_violation
    //（与 UI 表单提交同一服务端裁决面）
    expect(seeded.createdWfid, 'seed captured a created wfid').toBeTruthy();
    const illegal = await page.request.post(
      `${API_ORIGIN}/api/v1/chat/sessions/${encodeURIComponent(sid)}/feature-edits/preview`,
      { headers: { Authorization: `Bearer ${bearer}` },
        data: { target: { ref_id: seededRef, feature_ids: [seeded.createdWfid] },
          ops: [{ op: 'set_fields', fields: { capacity: -1 },
            wfids: [seeded.createdWfid] }] } },
    );
    expect(illegal.ok(), `preview responds 200 (typed findings face): ${illegal.status()}`).toBeTruthy();
    const illegalBody = (await illegal.json()) as {
      committable?: boolean; validation?: Array<{ code?: string }>;
    };
    expect(illegalBody.committable).toBe(false);
    expect(illegalBody.validation?.map((v) => v.code)).toContain('contract_violation');
  });
});
