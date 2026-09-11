/**
 * Journey bootstrap helpers: deterministic context, session identity, and the
 * pre-paint theme probe used by journey 5.
 */
import type { Page } from 'playwright/test';
import { expect } from 'playwright/test';
import { installJourneyStubs, JourneyWorld, defaultWorld } from '../fixtures/api-stubs';

export { JourneyWorld, defaultWorld };

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
 * Skips with an explicit reason when credentials are absent — never silently.
 */
export async function loginViaApi(page: Page): Promise<void> {
  const user = process.env.E2E_USER;
  const password = process.env.E2E_PASS;
  if (!user || !password) {
    throw new Error(
      'real-mode journeys require E2E_USER/E2E_PASS (nightly lane provisions them via manage.py create_admin)',
    );
  }
  const res = await page.request.post('/api/v1/auth/login', {
    data: { identifier: user, password },
  });
  if (!res.ok()) {
    throw new Error(`real-mode login failed: ${res.status()} (check nightly provisioning)`);
  }
  const tokens = (await res.json()) as {
    access_token: string;
    user: { id: string; username: string; display_name?: string; roles?: string[] };
  };
  await page.addInitScript(
    (seed: { token: string; authUser: unknown }) => {
      try {
        window.localStorage.setItem(
          'webgis_auth',
          JSON.stringify({ accessToken: seed.token, user: seed.authUser }),
        );
      } catch { /* surfaced by the 401 path */ }
    },
    { token: tokens.access_token, authUser: tokens.user },
  );
}

/** Seed the auth token store key before any app script runs (pre-hydration).
 * Shape mirrors lib/auth/tokenStore.ts persist(): flat {accessToken, user}. */
export async function seedAuthUser(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem(
        'webgis_auth',
        JSON.stringify({
          accessToken: 'e2e-journey-token',
          user: { id: 'u-e2e', username: 'e2e', display_name: 'E2E 用户', roles: ['user'] },
        }),
      );
    } catch { /* storage unavailable — journey will surface the 401 path */ }
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
