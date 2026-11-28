/* 必过旅程 3 —— 真实浏览器读者验证（Playwright + chromium headless）。
 *
 * 前置：backend journey 已产出 /tmp/pub-journey/{journey.db,data} 与
 * /tmp/pub-journey/evidence.json（share_code）。
 * 变体：桌面 1280×800 / 窄屏 390×844 / 键盘 Tab / 无 WebGL（等价视图）。
 * 证据：截图 + 断言清单 → /tmp/pub-journey/browser-evidence.json。
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const REPO = '/home/kevin/projects/webgis/webgis-interactive-map-publication-20261127';
const { chromium } = require(path.join(REPO, 'frontend', 'node_modules', 'playwright'));

const SCRATCH = '/tmp/pub-journey';
const BACKEND_PORT = process.env.JOURNEY_BACKEND_PORT || '8001';
const FRONTEND_PORT = '3101';

const backendEnv = {
  ...process.env,
  DATABASE_URL: `sqlite:///${SCRATCH}/journey.db`,
  DATA_DIR: `${SCRATCH}/data`,
  USE_REDIS: 'false',
  PI_AUTOSTART: 'false',
  JWT_SECRET_KEY: 'journey-secret-key-32-chars-ok-xxxx',
  ENV: 'development',
  LLM_API_KEY: 'test-key-not-real',
  OMP_NUM_THREADS: '2',
  OPENBLAS_NUM_THREADS: '2',
  GIS_MANIFEST_STRICT: '0',
  CORS_ORIGINS: JSON.stringify([
    'http://localhost:3000', 'http://127.0.0.1:3000',
    `http://localhost:${FRONTEND_PORT}`, `http://127.0.0.1:${FRONTEND_PORT}`,
  ]),
};

function waitPort(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const resp = await fetch(url);
        if (resp.ok) return resolve();
      } catch { /* not yet */ }
      if (Date.now() > deadline) return reject(new Error(`timeout waiting ${url}`));
      setTimeout(tick, 1000);
    };
    tick();
  });
}

async function main() {
  const evidence = { code: process.env.JOURNEY_CODE || '' };
  const shareCode = evidence.code || JSON.parse(fs.readFileSync(`${SCRATCH}/share_code.txt`, 'utf8')).code;

  const backend = spawn('.venv/bin/python', ['-m', 'uvicorn', 'app.main:app',
    '--host', '127.0.0.1', '--port', BACKEND_PORT, '--log-level', 'warning'], {
    cwd: REPO, env: backendEnv,
    stdio: ['ignore', fs.openSync(`${SCRATCH}/browser-server.log`, 'w'), 'pipe'],
  });
  const frontend = spawn('pnpm', ['exec', 'next', 'dev', '--port', FRONTEND_PORT], {
    cwd: `${REPO}/frontend`,
    env: { ...process.env, NEXT_PUBLIC_API_URL: `http://127.0.0.1:${BACKEND_PORT}` },
    stdio: ['ignore', fs.openSync(`${SCRATCH}/browser-frontend.log`, 'w'), 'pipe'],
  });

  try {
    await waitPort(`http://127.0.0.1:${BACKEND_PORT}/api/v1/health`, 120000);
    await waitPort(`http://127.0.0.1:${FRONTEND_PORT}/`, 180000);

    const browser = await chromium.launch({ headless: true });
    const results = {};

    // ── 桌面 ────────────────────────────────────────────────────────
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`http://127.0.0.1:${FRONTEND_PORT}/p/${shareCode}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.getByRole('heading', { name: '区县学校分布专题图' }).waitFor({ timeout: 30000 });
      results.title_visible = true;
      results.license_visible = await page.getByText('CC0-SIMULATED').first().isVisible();
      results.readonly_notice = await page.getByText('只读视图').first().isVisible();
      // 图例 tab（默认）：图例条目或空态存在
      results.legend_tab = await page.getByRole('tab', { name: '图例' }).isVisible();
      await page.screenshot({ path: `${SCRATCH}/desktop-reader.png`, fullPage: false });

      // 数据表 tab：服务端分页行
      await page.getByRole('tab', { name: '数据表' }).click();
      await page.getByText('第 1 页').waitFor({ timeout: 20000 });
      const rows = await page.locator('tbody tr').count();
      results.table_rows_page1 = rows;
      await page.screenshot({ path: `${SCRATCH}/desktop-table.png` });

      // 统计 tab：显式分母
      await page.getByRole('tab', { name: '统计' }).click();
      await page.getByText('分母：总数 12').waitFor({ timeout: 20000 });
      results.stats_denominator_visible = true;
      await page.screenshot({ path: `${SCRATCH}/desktop-stats.png` });

      // 说明 tab（WP4 等价信息）
      await page.getByRole('tab', { name: '说明' }).click();
      await page.getByText('合成区县数据（simulated）').first().waitFor({ timeout: 20000 });
      results.about_attribution_visible = true;
      results.accessibility_alt = await page.getByText('区县学校分布专题图', { exact: false }).first().isVisible();
      await page.screenshot({ path: `${SCRATCH}/desktop-about.png` });

      // 地图交互：canvas 存在（WebGL 环境）
      results.map_canvas = await page.locator('canvas').first().isVisible();
      await page.close();
    }

    // ── 键盘 ────────────────────────────────────────────────────────
    {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.goto(`http://127.0.0.1:${FRONTEND_PORT}/p/${shareCode}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('heading', { name: '区县学校分布专题图' }).waitFor({ timeout: 30000 });
      // Tab 依次可达：跳转链接 / 图层 checkbox / tablist
      let reachedTablist = false;
      for (let i = 0; i < 25; i++) {
        await page.keyboard.press('Tab');
        const focused = await page.evaluate(() => ({
          role: document.activeElement?.getAttribute('role'),
          tag: document.activeElement?.tagName,
          text: (document.activeElement?.textContent || '').slice(0, 12),
        }));
        if (focused.role === 'tab') { reachedTablist = true; break; }
      }
      results.keyboard_reaches_tabs = reachedTablist;
      // 键盘激活数据表 tab
      if (reachedTablist) {
        await page.keyboard.press('Enter');
        await page.waitForTimeout(500);
      }
      results.announce_region = await page.locator('[aria-live="polite"]').count();
      await page.screenshot({ path: `${SCRATCH}/keyboard-flow.png` });
      await page.close();
    }

    // ── 窄屏（移动布局） ────────────────────────────────────────────
    {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`http://127.0.0.1:${FRONTEND_PORT}/p/${shareCode}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('heading', { name: '区县学校分布专题图' }).waitFor({ timeout: 30000 });
      // 面板在小屏堆叠（aside 可见且可滚）
      results.narrow_aside_visible = await page.getByRole('tab', { name: '图例' }).isVisible();
      await page.screenshot({ path: `${SCRATCH}/narrow-reader.png` });
      await page.close();
    }

    // ── 无 WebGL（等价数据表视图） ──────────────────────────────────
    {
      const context = await browser.newContext({
        viewport: { width: 1280, height: 800 },
        launchOptions: { args: ['--disable-webgl', '--disable-webgl2', '--disable-3d-apis'] },
      });
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${FRONTEND_PORT}/p/${shareCode}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('heading', { name: '区县学校分布专题图' }).waitFor({ timeout: 30000 });
      await page.getByText('WebGL', { exact: false }).first().waitFor({ timeout: 15000 });
      results.nowebgl_banner = true;
      results.nowebgl_canvas = await page.locator('canvas').count();
      // 等价信息仍可得：数据表可用
      await page.getByRole('tab', { name: '数据表' }).click();
      await page.getByText('第 1 页').waitFor({ timeout: 20000 });
      results.nowebgl_table_rows = await page.locator('tbody tr').count();
      await page.screenshot({ path: `${SCRATCH}/nowebgl-reader.png` });
      await context.close();
    }

    await browser.close();
    results.ok = true;
    fs.writeFileSync(`${SCRATCH}/browser-evidence.json`, JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 2));
  } finally {
    frontend.kill('SIGTERM');
    backend.kill('SIGTERM');
    setTimeout(() => { try { frontend.kill('SIGKILL'); backend.kill('SIGKILL'); } catch { /* */ } }, 8000);
  }
}

main().catch((err) => { console.error(err); process.exit(1); });
