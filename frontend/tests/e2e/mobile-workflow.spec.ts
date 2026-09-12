// @ts-check
/**
 * 移动视口主流程冒烟（ADR-0144 / P8，§5 门禁：≤768px chat→分析→出图）。
 *
 * 运行（需本地 dev server，离线 fixture 与 visual corpus 同源）：
 *   cd frontend
 *   npx playwright test tests/e2e/mobile-workflow.spec.ts --project=chromium
 *
 * 或使用 visual corpus 采集器（离线确定性，含 mobile 档视口）：
 *   node test/visual/capture.mjs --only chat --viewports 390x844
 *
 * 断言目标：
 * 1. ≤768px 视口下布局进入 mobile 档（<html data-layout-mode="mobile"）
 * 2. NavRail 折叠为底部横栏（data-rail-variant="bottom"）
 * 3. chat 面板以 bottom-sheet 呈现（role=dialog + data-snap）
 * 4. 发送分析指令后地图画布保持全屏可见（无左 inset）
 */
import { test, expect } from '@playwright/test'

const MOBILE = { width: 390, height: 844 }

test.describe('mobile 主流程（≤768px）', () => {
  test.use({ viewport: MOBILE, hasTouch: true })

  test('chat→分析→出图 主流程可用', async ({ page }) => {
    await page.goto('/')

    // 1) mobile 档生效（use-layout-mode 驱动；html 标记由 page 壳层写入）
    await expect(page.locator('html')).toHaveAttribute('data-layout-mode', 'mobile', {
      timeout: 15_000,
    })

    // 2) NavRail 折叠为底部横栏
    const rail = page.locator('nav[data-rail-variant="bottom"]')
    await expect(rail).toBeVisible()

    // 3) 激活 chat tab → 面板以 bottom-sheet 呈现
    await page.getByRole('tab', { name: /对话|Chat/ }).click()
    const sheet = page.locator('[role="dialog"][data-snap]')
    await expect(sheet).toBeVisible()

    // 4) 发送指令 → 消息进入会话（地图画布保持无左 inset）
    await page.getByRole('textbox').first().fill('生成北京市海淀区缓冲区分析')
    await page.keyboard.press('Enter')
    await expect(page.getByText(/缓冲区|buffer/i).first()).toBeVisible()

    const mapCanvas = page.locator('#map-canvas')
    await expect(mapCanvas).toBeVisible()
    const box = await mapCanvas.boundingBox()
    expect(box?.x ?? 0).toBeLessThan(8) // 全屏优先：无桌面左栏推挤
  })

  test('语言切换 zh→en 即时生效（移动档）', async ({ page }) => {
    await page.goto('/')
    await page.evaluate(() => document.cookie = 'geoagent-locale=en-US; path=/')
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US')
  })
})
