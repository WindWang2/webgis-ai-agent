import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './transport';

// transport 层桩：这里只验证「回执不存在」与其它失败的区分，不触网。
vi.mock('./transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./transport')>();
  return {
    ...actual,
    apiFetch: vi.fn(),
  };
});

import { apiFetch } from './transport';
import { fetchTurnReceipt } from './turnContext';

const mockedFetch = vi.mocked(apiFetch);

describe('fetchTurnReceipt failure semantics', () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it('returns null for a 404 (legacy/absent receipt → coverage limited)', async () => {
    mockedFetch.mockRejectedValueOnce(new ApiError(404, 'Not Found'));
    await expect(fetchTurnReceipt('s1', 'turn-1')).resolves.toBeNull();
  });

  it('rethrows a 500 instead of pretending the receipt is missing', async () => {
    const failure = new ApiError(500, 'Internal Server Error');
    mockedFetch.mockRejectedValueOnce(failure);
    await expect(fetchTurnReceipt('s1', 'turn-1')).rejects.toBe(failure);
  });

  it('rethrows network failures (non-ApiError)', async () => {
    const failure = new TypeError('Failed to fetch');
    mockedFetch.mockRejectedValueOnce(failure);
    await expect(fetchTurnReceipt('s1', 'turn-1')).rejects.toBe(failure);
  });
});
