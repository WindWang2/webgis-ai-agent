/**
 * Journey assertion helpers (ADR-0146): every wait keys off a user-visible
 * production signal — no fixed sleeps, no store/window poking beyond the
 * documented pre-paint probe.
 */
import type { Page, Response } from 'playwright/test';
import { expect } from 'playwright/test';

/** The map canvas is mounted and MapLibre has created its GL context. */
export async function expectMapReady(page: Page): Promise<void> {
  const canvas = page.locator('.maplibregl-canvas');
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  // MapLibre only creates a real drawing buffer once initialized; a 0×0
  // canvas means the map never attached (e.g. container collapsed).
  await expect
    .poll(async () => {
      const box = await canvas.boundingBox();
      return box ? box.width > 0 && box.height > 0 : false;
    }, { timeout: 15_000 })
    .toBe(true);
}

/** A named layer became visible in the 图层 tab list (production mount path).
 * The layer row's select control is a checkbox whose accessible name is
 * `选择 {layer.name}` — chat-mounted layers are named 分析结果: {tool}. */
export async function expectLayerListed(page: Page, layerName: string | RegExp): Promise<void> {
  await page.locator('[role="tab"][aria-label*="图层"]').first().click();
  const row = page.getByRole('checkbox', { name: layerName });
  await expect(row.first()).toBeVisible({ timeout: 20_000 });
}

/** The results registry filled (rail tab badge shows a count). */
export async function expectResultsArrived(page: Page): Promise<void> {
  // The analysis result card on the map is the live production surface for a
  // landed step_result (the results workbench rail tab no longer exists in the
  // current shell — see j1 KNOWN GAP notes). The card's accessible name is
  // derived from its content ("分析结果: {tool}"), not an aria-label attribute.
  const card = page.getByRole('button', { name: /^分析结果/ });
  await expect(card.first()).toBeVisible({ timeout: 30_000 });
}

/**
 * Wait for the task center to reflect a given job state by job name.
 * `state` matches the visible status text on the job card.
 */
export async function expectJobState(
  page: Page,
  jobName: string | RegExp,
  state: string | RegExp,
): Promise<void> {
  // Idempotent navigation: clicking the already-active rail tab toggles the
  // panel closed (nav-rail collapse behaviour) — only switch when needed.
  const tab = page.locator('[role="tab"][aria-label*="任务"]').first();
  if ((await tab.getAttribute('aria-selected')) !== 'true') {
    await tab.click();
  }
  const card = page
    .locator('[data-testid^="job-card-"]')
    .filter({ hasText: jobName });
  await expect(card.first()).toBeVisible({ timeout: 15_000 });
  await expect(card.first()).toContainText(state, { timeout: 30_000 });
}

/** A chat turn started (SSE opened and first bytes processed) in real mode. */
export async function waitForChatStreamResponse(page: Page): Promise<Response> {
  return page.waitForResponse(
    (r) => /\/api\/v1\/chat\/stream$/.test(new URL(r.url()).pathname) && r.status() === 200,
    { timeout: 30_000 },
  );
}

/**
 * Export landed: POST /api/v1/export returned a url+filename, and the
 * download route serves a PNG (magic header + non-trivial size). Works in both
 * modes: mock answers from fixtures; real from the backend — the assertion is
 * the same production contract (exporter.ts:1181 → authenticated download).
 */
export async function expectExportLanded(page: Page): Promise<{ url: string; filename: string }> {
  const exportResponse = await page.waitForResponse(
    (r) =>
      r.request().method() === 'POST' &&
      /\/api\/v1\/export$/.test(new URL(r.url()).pathname) &&
      r.status() === 200,
    { timeout: 45_000 },
  );
  const payload = (await exportResponse.json()) as { url: string; filename: string };
  expect(payload.filename).toBeTruthy();
  expect(payload.url).toContain('/api/v1/export/download/');

  // Fetch the artifact the same way the authenticated-download helper does and
  // assert PNG magic bytes (PNG \x89PNG\r\n\x1a\n) — the 落盘 contract.
  const bytes = await page.evaluate(async (downloadPath) => {
    const tokenRaw = window.localStorage.getItem('webgis_auth');
    const headers: Record<string, string> = {};
    try {
      const token = tokenRaw ? (JSON.parse(tokenRaw)?.accessToken as string) : undefined;
      if (token) headers.Authorization = `Bearer ${token}`;
    } catch { /* anonymous */ }
    const res = await fetch(downloadPath, { headers });
    const buf = await res.arrayBuffer();
    return {
      status: res.status,
      contentType: res.headers.get('content-type') ?? '',
      size: buf.byteLength,
      magic: Array.from(new Uint8Array(buf.slice(0, 8)))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join(''),
    };
  }, payload.url);
  expect(bytes.status).toBe(200);
  expect(bytes.magic).toBe('89504e470d0a1a0a');
  expect(bytes.size).toBeGreaterThan(0);
  return payload;
}

/** Toast confirming an action landed (templates apply, share copy, …). */
export async function expectToast(page: Page, text: string | RegExp): Promise<void> {
  const toast = page.locator('[role="status"], [data-testid^="toast"]').filter({ hasText: text });
  await expect(toast.first()).toBeVisible({ timeout: 15_000 });
}
