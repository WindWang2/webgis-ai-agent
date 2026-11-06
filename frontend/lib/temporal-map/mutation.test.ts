import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api/transport';
import { emitTemporalWindowSettled } from '@/lib/interaction/bus';
import { commitMapSpecDocument } from '@/lib/mapspec/session-cursor';
import { commitUserTemporal } from './mutation';

const session = vi.hoisted(() => ({
  cursor: { sessionId: 'session-a', revision: 2, ownerToken: 'owner-a', mapspecFingerprint: undefined },
  listeners: new Set<() => void>(),
}));

vi.mock('@/lib/api/transport', () => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/interaction/bus', () => ({ emitTemporalWindowSettled: vi.fn() }));
vi.mock('@/lib/mapspec/user-mutation', () => ({
  newMutationId: () => 'temporal-mutation',
  enqueueUserMutation: vi.fn((operation: () => Promise<unknown>) => operation()),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => session.cursor,
  setMapSpecRevision: vi.fn(),
  commitMapSpecDocument: vi.fn(),
  subscribeMapSpecLive: (listener: () => void) => {
    session.listeners.add(listener);
    return () => { session.listeners.delete(listener); };
  },
}));

function switchSession(sessionId: string): void {
  session.cursor = { ...session.cursor, sessionId };
  for (const listener of session.listeners) listener();
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  session.cursor = { sessionId: 'session-a', revision: 2, ownerToken: 'owner-a', mapspecFingerprint: undefined };
  session.listeners.clear();
});

describe('temporal mutation interaction ownership', () => {
  it.each([409, 500])('does not emit an old failure into the next session (%i)', async (status) => {
    const request = deferred<unknown>();
    vi.mocked(apiFetch).mockReturnValueOnce(request.promise);
    const pending = commitUserTemporal({ cursor_frame: '2021' });
    switchSession('session-b');
    request.reject({ status });
    expect((await pending).outcome).toBe('failed');
    expect(emitTemporalWindowSettled).not.toHaveBeenCalled();
    expect(commitMapSpecDocument).not.toHaveBeenCalled();
    expect(session.listeners.size).toBe(0);
  });

  it('rejects an old success after switching away and back to the same session', async () => {
    const request = deferred<unknown>();
    vi.mocked(apiFetch).mockReturnValueOnce(request.promise);
    const pending = commitUserTemporal({ cursor_frame: '2021' });
    switchSession('session-b');
    switchSession('session-a');
    request.resolve({ mapspec: { layers: [] }, mutation_revision: 9 });
    expect((await pending).outcome).toBe('failed');
    expect(emitTemporalWindowSettled).not.toHaveBeenCalled();
    expect(commitMapSpecDocument).not.toHaveBeenCalled();
    expect(session.listeners.size).toBe(0);
  });

  it('records the settled event when the original session remains active', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ mapspec: { layers: [] }, mutation_revision: 3 });
    expect((await commitUserTemporal({ cursor_frame: '2021' })).outcome).toBe('committed');
    expect(emitTemporalWindowSettled).toHaveBeenCalledWith(
      { current: '2021' },
      { clientMutationId: 'temporal-mutation', outcome: 'committed', mutationRevision: 3 },
    );
    expect(session.listeners.size).toBe(0);
  });
});
