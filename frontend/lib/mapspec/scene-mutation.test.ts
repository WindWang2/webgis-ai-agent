/**
 * G3D05（ADR-0275 D3）：用户场景 mutation 客户端通道测试。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/transport', () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(status: number, statusText: string, body?: unknown) {
      super(`ApiError ${status}`);
      this.status = status;
      this.statusText = statusText;
      this.body = body;
    }
  }
  return {
    ApiError,
    apiFetch: vi.fn(),
  };
});

vi.mock('@/lib/mapspec/session-cursor', () => {
  let revision = 0;
  let committed: unknown = null;
  const cursor = { sessionId: 'sess-1', revision: 0, ownerToken: null };
  return {
    getMapSpecSessionCursor: vi.fn(() => cursor),
    setMapSpecRevision: vi.fn((rev: number) => {
      if (Number.isFinite(rev) && rev >= revision) {
        revision = rev;
        cursor.revision = rev;
      }
    }),
    commitMapSpecDocument: vi.fn((spec: unknown) => {
      committed = spec;
      return true;
    }),
    __getCommitted: () => committed,
  };
});

vi.mock('@/lib/mapspec/user-mutation', () => ({
  newMutationId: () => 'test-mutation-id-0001',
  enqueueUserMutation: vi.fn(<T,>(op: () => Promise<T>) => op()),
}));

import { apiFetch, ApiError } from '@/lib/api/transport';
import { commitMapSpecDocument, setMapSpecRevision } from '@/lib/mapspec/session-cursor';
import { commitUserScene } from '@/lib/mapspec/scene-mutation';

const apiFetchMock = vi.mocked(apiFetch);

describe('commitUserScene', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('posts set_scene with cursor revision and client id', async () => {
    apiFetchMock.mockResolvedValueOnce({
      success: true,
      mutation_revision: 7,
      mapspec: { layers: [], scene: { mode: '3d' } },
    });
    const result = await commitUserScene({ mode: '3d', camera: { pitch: 50 } });
    expect(result.outcome).toBe('committed');
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [path, opts] = apiFetchMock.mock.calls[0] as unknown as [
      string, { method: string; body: Record<string, unknown> },
    ];
    expect(path).toBe('/api/v1/chat/sessions/sess-1/mapspec/mutations');
    expect(opts.body.intent).toBe('set_scene');
    expect(opts.body.expected_revision).toBe(0);
    expect(opts.body.client_mutation_id).toBe('test-mutation-id-0001');
    expect(setMapSpecRevision).toHaveBeenCalledWith(7);
    expect(commitMapSpecDocument).toHaveBeenCalledWith(
      expect.objectContaining({ scene: { mode: '3d' } }),
      7,
    );
  });

  it('clears scene when payload is null', async () => {
    apiFetchMock.mockResolvedValueOnce({
      success: true,
      mutation_revision: 8,
      mapspec: { layers: [] },
    });
    const result = await commitUserScene(null);
    expect(result.outcome).toBe('committed');
    const [, opts] = apiFetchMock.mock.calls[0] as unknown as [
      string, { body: Record<string, unknown> },
    ];
    expect(opts.body.scene).toBeNull();
  });

  it('409 superseded rebases to server truth without replay', async () => {
    apiFetchMock.mockRejectedValueOnce(
      new ApiError(409, 'Conflict', {
        detail: {
          success: false,
          status: 'superseded',
          mutation_revision: 12,
          mapspec: { layers: [], scene: { mode: '2d' } },
        },
      }),
    );
    const result = await commitUserScene({ mode: '3d' });
    expect(result.outcome).toBe('superseded');
    expect(result.revision).toBe(12);
    expect(setMapSpecRevision).toHaveBeenCalledWith(12);
    expect(commitMapSpecDocument).toHaveBeenCalled();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
  });

  it('non-409 failure reports failed without touching committed spec', async () => {
    apiFetchMock.mockRejectedValueOnce(
      new ApiError(400, 'Bad Request', { detail: { success: false } }),
    );
    const result = await commitUserScene({ mode: '4d' } as never);
    expect(result.outcome).toBe('failed');
    expect(commitMapSpecDocument).not.toHaveBeenCalled();
    expect(setMapSpecRevision).not.toHaveBeenCalled();
  });

  it('detached session fails fast without a request', async () => {
    const { getMapSpecSessionCursor } = await import(
      '@/lib/mapspec/session-cursor');
    vi.mocked(getMapSpecSessionCursor).mockReturnValueOnce({
      sessionId: undefined, revision: 0, ownerToken: null,
    } as never);
    const result = await commitUserScene({ mode: '3d' });
    expect(result.outcome).toBe('failed');
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
