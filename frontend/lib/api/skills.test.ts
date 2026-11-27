import { afterEach, describe, expect, it, vi } from 'vitest';
import { getSkills } from './skills';
import { clearCache } from './get-fast-path';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

afterEach(() => {
  clearCache();
  vi.clearAllMocks();
});

describe('getSkills', () => {
  it('returns the skills array from the payload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({ skills: [{ name: 'map', description: 'd' }] })),
    });
    await expect(getSkills()).resolves.toEqual([{ name: 'map', description: 'd' }]);
  });

  it('degrades to an empty list on a 204 / empty body instead of throwing TypeError', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: 'No Content',
      text: () => Promise.resolve(''),
    });
    await expect(getSkills()).resolves.toEqual([]);
  });

  it('degrades to an empty list when the payload has no skills key', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({})),
    });
    await expect(getSkills()).resolves.toEqual([]);
  });
});
