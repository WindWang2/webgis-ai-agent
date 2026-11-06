import type { Page } from 'playwright/test';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('playwright/test', async () => ({ expect: (await import('vitest')).expect }));
vi.mock('./bootstrap', () => ({ getJourneyAuth: () => ({ accessToken: 'issued-token' }) }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('journey export download', () => {
  it('downloads the PNG from the backend response origin, not the frontend origin', async () => {
    const fetch = vi.fn(async () => ({
      status: 200,
      headers: new Headers({ 'content-type': 'image/png' }),
      arrayBuffer: async () => Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0]).buffer,
    }));
    vi.stubGlobal('fetch', fetch);

    const payload = { url: '/api/v1/export/download/map.png', filename: 'map.png' };
    const page = {
      waitForResponse: vi.fn(async () => ({
        json: async () => payload,
        url: () => 'http://127.0.0.1:18001/api/v1/export',
      })),
      evaluate: vi.fn(async (callback: (url: string) => Promise<unknown>, url: string) => callback(url)),
    };
    const { expectExportLanded } = await import('./assertions');
    await expect(expectExportLanded(page as unknown as Page)).resolves.toEqual(payload);
    expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:18001/api/v1/export/download/map.png', {
      headers: { Authorization: 'Bearer issued-token' },
    });
  });
});
