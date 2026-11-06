/**
 * Journey bootstrap helpers: deterministic context, session identity, and the
 * pre-paint theme probe used by journey 5.
 */
import type { Page } from 'playwright/test';
import { expect } from 'playwright/test';
import { installJourneyStubs, JourneyWorld, defaultWorld } from '../fixtures/api-stubs';
import { API_ORIGIN } from './mode';

export { JourneyWorld, defaultWorld };

interface JourneyAuth {
  accessToken: string;
  user: { id: string; username: string; role?: string };
}
const journeyAuth = new WeakMap<Page, JourneyAuth>();

/** Test-runner credential state; never browser storage. */
export function getJourneyAuth(page: Page): JourneyAuth | undefined {
  return journeyAuth.get(page);
}

async function prepareAuth(page: Page, auth: JourneyAuth): Promise<void> {
  journeyAuth.set(page, auth);
  // Story routes have no account panel. Emulate the documented opener
  // handshake there, so the production receiver installs tokens in memory.
  await page.addInitScript((seed) => {
    if (window.location.pathname !== '/story' || window.opener) return;
    const opener = {
      postMessage(request: { type: string; sessionId: string; nonce: string }) {
        if (request.type !== 'webgis:story-owner-token:request') return;
        window.dispatchEvent(new MessageEvent('message', {
          source: opener as unknown as Window,
          origin: window.location.origin,
          data: { type: 'webgis:story-owner-token:response', sessionId: request.sessionId,
            nonce: request.nonce, token: null, accessToken: seed.accessToken, user: seed.user },
        }));
      },
    };
    Object.defineProperty(window, 'opener', { configurable: true, value: opener });
  }, auth);
}

export async function copyJourneyAuth(source: Page, target: Page): Promise<void> {
  const auth = journeyAuth.get(source);
  if (auth) await prepareAuth(target, auth);
}

/**
 * Mock-mode bootstrap: install stubs and start authenticated (export button
 * needs an auth user; seeding the token store is the same channel the real
 * login flow writes through).
 */
export async function bootstrapMock(page: Page, world: JourneyWorld): Promise<void> {
  await seedAuthUser(page);
  await installJourneyStubs(page, world);
}

/**
 * Real-mode auth: sign in through the real backend /auth/login with the
 * credentials the nightly lane provisions (manage.py create_admin) and write
 * the result through the same tokenStore channel the UI login uses.
 * Fails with an explicit reason when credentials are absent.
 */
export async function loginViaApi(page: Page): Promise<string> {
  const user = process.env.E2E_USER;
  const password = process.env.E2E_PASS;
  if (!user || !password) {
    throw new Error(
      'real-mode journeys require E2E_USER/E2E_PASS (nightly lane provisions them via manage.py create_admin)',
    );
  }
  // page.request inherits the frontend baseURL, which has no backend proxy
  // in the nightly split-port stack. Use the same backend origin as the UI.
  const res = await page.request.post(new URL('/api/v1/auth/login', API_ORIGIN).href, {
    data: { identifier: user, password },
  });
  if (!res.ok()) {
    throw new Error(`real-mode login failed: ${res.status()} (check nightly provisioning)`);
  }
  const tokens = (await res.json()) as {
    access_token: string;
    user: { id: string; username: string; display_name?: string; roles?: string[] };
  };
  await prepareAuth(page, { accessToken: tokens.access_token, user: tokens.user });
  // API fixture setup needs the same authenticated owner as the browser.
  return tokens.access_token;
}

/** Queue a mock login response; the app consumes it through its Account form. */
export async function seedAuthUser(page: Page): Promise<void> {
  await prepareAuth(page, {
    accessToken: 'e2e-journey-token',
    user: { id: 'u-e2e', username: 'e2e', role: 'user' },
  });
}

/**
 * Wait for the app shell to be interactive: the chat composer textarea is the
 * entry surface every journey passes through and only exists post-hydration.
 */
export async function awaitShellReady(page: Page): Promise<void> {
  await expect(
    page.locator('textarea[aria-label="输入空间分析指令"]'),
  ).toBeVisible({ timeout: 30_000 });
  const auth = journeyAuth.get(page);
  if (auth) {
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.locator('#settings-tab-account').click();
    const loginForm = page.locator('#auth-identifier');
    if (await loginForm.isVisible()) {
      await page.route('**/api/v1/auth/login', (route) => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ access_token: auth.accessToken, token_type: 'bearer', expires_in: 1800, user: auth.user }),
      }), { times: 1 });
      await loginForm.fill(auth.user.username);
      await page.locator('#auth-password').fill('journey-fixture-login');
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeVisible();
    }
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
  }
}

/**
 * Open a journey chat turn: fill the composer and send through the real
 * production path (no store poking). Resolves after the send click lands; the
 * turn's effects are asserted by the caller via UI signals.
 */
export async function sendChat(page: Page, message: string): Promise<void> {
  const input = page.locator('textarea[aria-label="输入空间分析指令"]');
  await expect(input).toBeVisible();
  await input.fill(message);
  await page.locator('button[aria-label="发送消息"]').click();
}

/**
 * Navigate a rail tab by its visible label (the way users reach surfaces).
 * Clicking the already-active tab toggles some panels closed, so this is
 * idempotent-guarded by the caller's expectations rather than double-clicked.
 */
export async function openRailTab(page: Page, label: string): Promise<void> {
  const tab = page.locator(`[role="tab"][aria-label*="${label}"]`).first();
  await expect(tab).toBeVisible({ timeout: 15_000 });
  await tab.click();
}

/**
 * Pre-paint theme probe (journey 5): records the documentElement theme state
 * at `readystatechange → interactive` — parse complete. By spec the inline
 * no-flash script in app/layout.tsx is parser-blocking, so it has already run
 * at this instant; deferred/module scripts (React) have not. This is the
 * earliest JS-observable, race-free instant proving the theme was applied by
 * the bootstrap rather than by a hydration effect — the「不闪白」contract.
 */
export async function installThemeProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    const snap = (): Record<string, unknown> => {
      const root = document.documentElement;
      return {
        dark: root.classList.contains('dark'),
        theme: root.getAttribute('data-theme'),
        accent: root.style.getPropertyValue('--agent-accent-raw'),
      };
    };
    w.__e2eThemeAtStart = null;
    const settle = () => {
      if (document.readyState === 'interactive' && w.__e2eThemeAtStart === null) {
        w.__e2eThemeAtStart = snap();
      }
    };
    document.addEventListener('readystatechange', settle);
    if (document.readyState === 'interactive' || document.readyState === 'complete') {
      settle();
    }
  });
}
