import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn(async () => ({}));
vi.mock('@/lib/api/transport', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

import { previewFeatureEdit } from './feature-edit';

beforeEach(() => {
  apiFetch.mockClear();
});

describe('feature-edit path encoding', () => {
  it('encodes sessionId', async () => {
    await previewFeatureEdit('a/b?c', { refId: 'r' }, []);
    expect(String(apiFetch.mock.calls[0]?.[0])).toContain('/sessions/a%2Fb%3Fc/feature-edits/preview');
  });
});
