/**
 * Journey E2E config (quality-e2e-v9, ADR-0146).
 *
 * Deliberately separate from the runtime validator: the validator drives
 * compiled MapSpec dist pages to gate the render pipeline; the journeys here
 * drive the real app shell to gate user paths (chat → map → export …).
 *
 * Dual mode (env E2E_MODE):
 *  - mock (default): every product API is answered by fixtures in
 *    e2e/fixtures/api-stubs.ts via page.route — no backend needed. Runs on PRs
 *    (smoke subset) and locally.
 *  - real: a real backend (uvicorn, minimal SQLite/fakeredis config, LLM
 *    boundary stubbed deterministically) must already be listening at
 *    E2E_API_URL (default :8001) and the frontend at the configured baseURL.
 *    Runs on the nightly quality-e2e lane with REQUIRE_BROWSER=1 (missing
 *    browser = hard red, never a green skip — same contract as the
 *    runtime-validator lane).
 *
 * Specs are named `*.journey.ts` so vitest's default include
 * (`*.{test,spec}.{ts,tsx}` patterns) never picks them up, and vitest's
 * files are never collected by Playwright (testMatch below).
 */
import { defineConfig, devices } from 'playwright/test';

const MODE = process.env.E2E_MODE === 'real' ? 'real' : 'mock';
const PORT = Number(process.env.E2E_PORT ?? 3310);
const BASE = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.journey.ts',
  // Journeys own their deterministic waits; the global cap only guards against
  // a wedged page (never tuned to pass — see ADR-0146 determinism section).
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  // No global retries in CI config: flaky = red here. A journey that proves
  // flaky gets an explicit `test.info().retry` note in the flaky register and
  // a determinism fix, not a silent second attempt.
  retries: 0,
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list']],
  outputDir: 'test-results/e2e',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    // Journey determinism: animations/motion must not introduce frame races.
    reducedMotion: 'reduce',
    // StoryMap share journey asserts the copied URL via the clipboard.
    permissions: ['clipboard-read', 'clipboard-write'],
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  ...(MODE === 'mock'
    ? {
        webServer: {
          command: `pnpm exec next dev -p ${PORT}`,
          url: BASE,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          stdout: 'pipe',
          stderr: 'pipe',
        },
      }
    : {}),
});
