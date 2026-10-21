/**
 * Journey 8 — 移动档（≤768px）主流程（ADR-0144 / P8，§5 门禁）。
 *
 * ≤768px 是产品的一整类视口：NavRail 折叠为底部横栏、chat 面板 bottom-sheet
 * 化、地图全屏无 inset。本 journey 驱动真实用户路径守护该档布局契约：
 * 1. mobile 档生效（<html data-layout-mode="mobile">，use-layout-mode 驱动）
 * 2. NavRail 折叠为底部横栏（data-rail-variant="bottom"）
 * 3. 打开工作面板 → chat 以 bottom-sheet 呈现（role=dialog + data-snap）
 * 4. 发送分析指令 → SSE 回放 → 结果卡片落地图 → 画布保持全屏（无左 inset）
 * 5. 语言切换经设置面板真实 UI（zh→en）即时生效 —— 不直写 cookie（#1555）
 *
 * 面板入口说明：rail 底部档 chat tab 直开（#1666 修复 tablist 方向不随
 * variant 切换后，chat 不再被 48px 底栏裁剪/地图 canvas 拦截）；语言切换
 * 走顶栏「设置」真实入口（#1667 修复右侧集群 390px 溢出后的内联按钮）。
 *
 * 本地运行（mock 档零后端，webServer 自动起 next dev）：
 *   cd frontend
 *   pnpm exec playwright test j8
 *
 * 环境记录（2026-09-29，j7 同款）：`next dev` 默认 Turbopack 编译
 * maplibre-gl@6 动态 worker blob 失败（master 基线同样失败，工具链漂移）——
 * 先 `pnpm exec next dev --webpack -p 3310` 起服，config 的
 * reuseExistingServer 会复用已起服务。
 *
 * （历史 NOTE，2026-10-18 本 journey 激活时实测的移动档残疵，均已修复：)
 * - 原 NOTE 1「nav-rail 底部档 tablist className 恒 flex-col、14 个 tab
 *   纵向堆叠被裁剪、chat 不可点」→ #1666：方向随 variant 切换（底部档
 *   flex-row + 横向滚动），本 journey 步骤 3 已改回 rail chat tab 直开。
 * - 原 NOTE 2「top-bar 右侧集群 ~530px 溢出、设置不可达」→ #1667：mobile
 *   档折叠为「更多」收纳菜单，见下方 390px 溢出断言。
 */
import { test, expect } from 'playwright/test';
import { bootstrapMock, defaultWorld, awaitShellReady, sendChat } from '../helpers/bootstrap';
import { analysisTurn } from '../fixtures/sse';
import { expectMapReady, expectResultsArrived } from '../helpers/assertions';
import { MODE, MOCK_ONLY_REASON } from '../helpers/mode';

/** iPhone 12/13 逻辑视口（≤768px mobile 档；与 visual corpus mobile 档同值）。 */
const MOBILE = { width: 390, height: 844 };

test.describe('journey-8 移动档主流程（≤768px）', () => {
  test.use({ viewport: MOBILE, hasTouch: true });

  test('mock：mobile 档布局 → chat bottom-sheet → 分析出图 → 地图全屏无 inset', async ({ page }) => {
    test.skip(MODE !== 'mock', MOCK_ONLY_REASON);
    const world = defaultWorld();
    // 缓冲区分析回合：与桌面档同一条生产 SSE 契约（fixtures/sse.ts）——
    // mock 档只替换传输层，交互与状态机全是生产路径。
    world.pushChat(
      analysisTurn({
        sessionId: 's-j8',
        taskId: 't-j8',
        summary: '缓冲区分析完成，结果已挂载为图层。',
      }),
    );
    await bootstrapMock(page, world);
    await page.goto('/');

    // 1) mobile 档生效（use-layout-mode 驱动；html 标记由 page 壳层写入）
    await expect(page.locator('html')).toHaveAttribute('data-layout-mode', 'mobile', {
      timeout: 15_000,
    });

    // 2) NavRail 折叠为底部横栏（safe-area 内边距由组件自负责）
    const rail = page.locator('nav[data-rail-variant="bottom"]');
    await expect(rail).toBeVisible();

    // 2b) 验收 1（#1666）：chat tab 命中测试通过 —— tab 中心点的
    //     elementFromPoint 必须是 tab 自身（或其子元素），不被地图 canvas
    //     拦截。修复前 tablist 纵向堆叠被 48px 底栏裁剪，中心点落到 canvas 上，
    //     点击被拦截、chat 实际不可点（issue 的实证方法同款）。
    const chatTab = page.getByRole('tab', { name: '对话', exact: true });
    await expect(chatTab).toBeVisible();
    const hit = await chatTab.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return top === el || el.contains(top);
    });
    expect(hit, 'chat tab 命中点不应被地图 canvas 拦截').toBe(true);

    // 3) 打开工作面板：rail 底部档 chat tab 直开（真实用户路径 —— #1666
    //    修复前 tablist 纵向堆叠被 48px 底栏裁剪、点击被地图 canvas 拦截，
    //    只能绕行顶栏「展开侧栏」）。默认活动 tab 即 chat（useHudStore 初值），
    //    mobile 档（<768px）面板初值折叠 → 点击 active tab 即开面板，chat 以
    //    bottom-sheet 呈现，sheet 内直接是对话面板。
    await page.getByRole('tab', { name: '对话', exact: true }).click();
    const sheet = page.locator('[role="dialog"][data-snap]');
    await expect(sheet).toBeVisible();
    await expect(sheet).toHaveAttribute('data-snap', 'half');

    // 4) 发送指令 → SSE 回放 → 结果卡片落地图（生产 step_result 路径）
    await awaitShellReady(page);
    await sendChat(page, '生成北京市海淀区缓冲区分析');
    await expect(page.getByText(/缓冲区/).first()).toBeVisible();
    await expectResultsArrived(page);

    // 5) 地图画布保持全屏可见（mobile 档 inset 恒 0：无桌面左栏推挤）
    await expectMapReady(page);
    const box = await page.locator('#map-canvas').boundingBox();
    expect(box?.x ?? 0).toBeLessThan(8);
  });

  test('mock：390px 顶栏无横向溢出，「设置」按钮可达（#1667）', async ({ page }) => {
    test.skip(MODE !== 'mock', MOCK_ONLY_REASON);
    await bootstrapMock(page, defaultWorld());
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('data-layout-mode', 'mobile', {
      timeout: 15_000,
    });

    // 验收 2：顶栏无横向溢出。修复前右侧集群（新建/历史/底图/3D/UI 调整/
    // 设置）固有宽度 ~530px，390px 视口下整体溢出屏外；折叠后集群只剩
    // 新建/更多/设置三个 26px 按钮（宽控件收进「更多」收纳菜单）。
    const bar = page.getByTestId('topbar');
    await expect(bar).toBeVisible();
    const overflow = await bar.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow, 'top-bar 不应有横向溢出').toBeLessThanOrEqual(0);

    // 验收 1：「设置」在 390px 视口内可见且可点 —— 真实用户路径进设置抽屉。
    const settings = page.getByRole('button', { name: '设置', exact: true });
    await expect(settings).toBeVisible();
    await settings.click();
    await expect(
      page.locator('[role="dialog"][aria-labelledby="settings-panel-title"]'),
    ).toBeVisible();
  });

  test('mock：语言切换经设置面板真实 UI，zh→en 即时生效', async ({ page }) => {
    test.skip(MODE !== 'mock', MOCK_ONLY_REASON);
    await bootstrapMock(page, defaultWorld());
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('data-layout-mode', 'mobile', {
      timeout: 15_000,
    });

    // 顶栏「设置」→ 设置抽屉 → 「系统」分区的语言 radiogroup —— 全程真实
    // 用户路径（#1555：禁止 page.evaluate 直写 cookie 绕过语言契约）。
    // #1667 前 390px 下顶栏「设置」被溢出遮挡不可达，本用例只能绕道
    // Ctrl+K 命令面板；集群折叠修复后改回顶栏真实入口。
    await page.getByRole('button', { name: '设置', exact: true }).click();

    // 抽屉的可访问名随语言切换（标题走 i18n）—— 用稳定的 labelledby 锚点定位，
    // 名称基定位会在切换后瞬间失效。
    const drawer = page.locator('[role="dialog"][aria-labelledby="settings-panel-title"]');
    await expect(drawer).toBeVisible();
    await drawer.getByRole('tab', { name: '系统', exact: true }).click();
    await drawer.getByRole('radio', { name: 'English', exact: true }).click();

    // <html lang> 由 setLocale 即时写入（applyLocaleToDom），界面文案随之切换。
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US');
    await expect(drawer.getByRole('radio', { name: 'English', exact: true })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    // 整树 i18n 重渲染的可见证据：语言提示文案切到英文（中文态不出现）。
    await expect(drawer.getByText('Interface language.')).toBeVisible();
  });
});
