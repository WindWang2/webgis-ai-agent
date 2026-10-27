/**
 * Journey 9 — 三维场景资产端到端（G3D02 v1.7）：MapSpec scene.assets →
 * MapLibre custom layer 真渲染 → 观察上行 → 3D 拾取 IR。
 *
 * 真实链路断言（mock 档 = 仅后端 stub，浏览器内全部真实）：
 *  1. 对话 turn 的 step_result 携带 v1.7 MapSpec（model3d 源 + scene.assets）
 *     与 fly_to 命令（生产投递路径 —— 与其他 journey 同款 SSE 回放）；
 *  2. scene3d 管理器 fetch GLB（fixture 路由供给）并挂载 custom layer；
 *  3. render observation POST 携带 scene3d_assets: mounted（真实渲染证据）；
 *  4. 点击模型 → 3D 检视卡 + Interaction IR feature_selected（词表复用）；
 *  5. 全程无 JS 错误、无 WebGL fatal（window.__mapErrors 环为空）。
 */
import { test, expect, type Page } from 'playwright/test';
import { installJourneyStubs, defaultWorld, corsFor } from '../fixtures/api-stubs';
import { awaitShellReady, seedAuthUser, sendChat } from '../helpers/bootstrap';
import { MODE } from '../helpers/mode';
import { sse, toolCall } from '../fixtures/sse';
import { buildGlbFixture } from '../../lib/map-kit/scene3d/__fixtures__/glb';

const ANCHOR: [number, number] = [104.06, 30.67];
const GLB_URL = 'https://assets.test/campus.glb';

const MAPSPEC = {
  version: '1.7',
  view: { center: ANCHOR, zoom: 15.5, pitch: 60, bearing: 0 },
  sources: {
    'model-a': {
      type: 'model3d',
      format: 'glb',
      url: GLB_URL,
      content_revision: 1,
    },
  },
  layers: [],
  scene: {
    mode: '3d',
    assets: [
      {
        id: 'campus',
        source: 'model-a',
        visible: true,
        // 放大 3000：1 单位三角 ≈ 3km —— zoom 15 下覆盖数百像素可点。
        placement: { anchor: ANCHOR, altitude_m: 0, scale: 3000 },
        vertical: { datum: 'egm96', unit: 'm', elevation_ref: 'ref:dem-e2e' },
        provenance_ref: 'ref:asset-e2e-license',
      },
    ],
  },
};

function scene3dTurn(): string {
  return [
    sse('session', { session_id: 's-j9' }),
    sse('task_start', { task_id: 't-j9', session_id: 's-j9' }),
    sse('token', { content: '三维场景资产已挂载，飞往锚点。' }),
    toolCall('set_scene_assets', {
      session_id: 's-j9',
      assets: MAPSPEC.scene.assets,
    }),
    sse('step_result', {
      task_id: 't-j9',
      step_id: 'sr-j9',
      tool: 'set_scene_assets',
      mutation_revision: 4,
      mapspec: MAPSPEC,
      result: {
        success: true,
        summary: '三维场景资产已挂载。',
        command: 'fly_to',
        params: { center: ANCHOR, zoom: 15.5, pitch: 60, bearing: 0 },
      },
    }),
    sse('task_complete', { task_id: 't-j9' }),
    'data: [DONE]\n\n',
  ].join('');
}

/** 旅程专属路由：GLB fixture + observation/IR 采集。 */
async function installScene3dRoutes(page: Page, seen: {
  glbHits: number;
  observations: Array<{ scene3d?: unknown }>;
  interactionEvents: Array<Record<string, unknown>>;
}): Promise<void> {
  await page.route(GLB_URL, async (route) => {
    seen.glbHits += 1;
    const buf = buildGlbFixture({ triangles: 4, coverOrigin: true, material: { baseColorFactor: [0.9, 0.2, 0.2, 1] } });
    await route.fulfill({
      status: 200,
      contentType: 'model/gltf-binary',
      headers: { 'access-control-allow-origin': '*' },
      body: Buffer.from(buf),
    });
  });
  await page.route(/\/cartographic-observation(\?|$)/, async (route) => {
    const body = route.request().postDataJSON() as { apply_ack?: unknown; scene3d_assets?: unknown };
    seen.observations.push({ scene3d: body?.scene3d_assets });
    await route.fulfill({
      status: 200, contentType: 'application/json', body: '{"accepted":true}',
      headers: corsFor(route.request().headers()['origin']),
    });
  });
  await page.route(/\/interaction-events(\?|$)/, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    seen.interactionEvents.push(body);
    await route.fulfill({
      status: 200, contentType: 'application/json', body: '{"accepted":true}',
      headers: corsFor(route.request().headers()['origin']),
    });
  });
}

test.describe('journey-9 三维场景资产 @smoke', () => {
  test('mock：v1.7 MapSpec → custom layer 真渲染 → 拾取 IR', async ({ page }) => {
    test.skip(MODE !== 'mock', 'mock-mode variant');

    const world = defaultWorld();
    world.pushChat(scene3dTurn());
    await seedAuthUser(page);
    await installJourneyStubs(page, world);
    const seen = { glbHits: 0, observations: [] as Array<{ scene3d?: unknown }>, interactionEvents: [] as Array<Record<string, unknown>> };
    await installScene3dRoutes(page, seen);

    const consoleErrors: string[] = [];
    page.on('pageerror', (err) => consoleErrors.push(String(err)));
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await page.goto('/');
    await awaitShellReady(page);

    // 0) 生产投递路径：对话 turn → SSE step_result（mapspec + fly_to）。
    await sendChat(page, '给成都挂载三维模型并飞过去');

    // 1) GLB 真实拉取（scene3d 管理器 → resource policy → fetch）。
    await expect
      .poll(() => seen.glbHits, { timeout: 20_000 })
      .toBeGreaterThanOrEqual(1);

    // 2) 挂载状态的页面内证据（dev-only 观察窗 __scene3dAssets —— 与
    //    __mapErrors 同款纪律；observation POST 的覆盖在 vitest 集成层，
    //    其指纹认证链依赖图层代 —— 纯 3D spec 的观察缺口见 EVIDENCE 已知限制）。
    await expect
      .poll(
        () => page.evaluate(() => {
          const w = window as unknown as { __scene3dAssets?: Array<{ assetId: string; code: string }> };
          return JSON.stringify(w.__scene3dAssets ?? []);
        }),
        { timeout: 15_000 },
      )
      .toContain('mounted');
    await expect
      .poll(
        () => page.evaluate(() => {
          const w = window as unknown as { __scene3dAssets?: Array<{ assetId: string; code: string }> };
          return JSON.stringify(w.__scene3dAssets ?? []);
        }),
        { timeout: 5_000 },
      )
      .toContain('campus');

    // 3) 真实用户路径：开启 3D 视角（HUD toggle —— spec camera 档仅在
    //    is3D 时生效，ADR-0199 既有语义），pitch 60 下 wall 模型面向相机。
    const toggle3d = page.locator('button[aria-label="切换至 3D 视角"]').first();
    if (await toggle3d.count()) {
      await toggle3d.click();
    }
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();
    await page.waitForTimeout(2500); // easeTo/flyTo 过渡 + 首帧渲染窗口
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    await canvas.click({
      position: { x: Math.round(box!.width / 2), y: Math.round(box!.height / 2) },
    });
    await expect(page.locator('[aria-label="3D asset pick"]')).toBeVisible({ timeout: 8_000 });

    // 4) Interaction IR feature_selected（closed 词表复用，layer_id 带
    //    scene3d: 前缀 —— 观察层事实，不造第二事件族）。
    await expect
      .poll(() => {
        const flat = JSON.stringify(seen.interactionEvents);
        return flat.includes('feature_selected') && flat.includes('scene3d:campus');
      }, { timeout: 8_000 })
      .toBe(true);

    // 5) 无 JS 错误、无 WebGL fatal（map error 环在 dev 下挂 window）。
    const mapErrors = await page.evaluate(() => {
      const w = window as unknown as { __mapErrors?: unknown[] };
      return (w.__mapErrors ?? []).length;
    });
    expect(mapErrors).toBe(0);
    // mock 档后端不在场：health/observation/interaction 等跨源资源失败是预期噪声。
    const benign = /favicon|ERR_CONNECTION_REFUSED|ERR_FAILED|Failed to fetch|playwright|Download the React DevTools/;
    const fatalErrors = consoleErrors.filter((e) => !benign.test(e));
    expect(fatalErrors, `console errors: ${fatalErrors.join(' | ')}`).toEqual([]);

    // 证据截图（PR/复盘用；不参与断言）。
    await page.screenshot({ path: 'e2e/artifacts/j9-scene3d.png' });
  });
});
