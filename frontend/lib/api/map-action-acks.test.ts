import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  classifyAckDelivery,
  createMapActionAckSender,
  DEFAULT_MAX_ATTEMPTS,
  DEFAULT_MAX_QUEUE,
} from './map-action-acks';
import type { MapActionAck } from './map-action-acks';
import { devOnly } from '@/lib/utils/logger';

function ack(actionId: string, extras: Partial<MapActionAck> = {}): MapActionAck {
  return { action_id: actionId, command: 'fly_to', status: 'succeeded', ...extras };
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  };
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe('classifyAckDelivery', () => {
  it('treats network errors, timeouts, 408, 429, 5xx, and dropped>0 as transient', () => {
    expect(classifyAckDelivery({ error: new TypeError('Failed to fetch') })).toEqual({
      kind: 'transient',
      reason: 'network',
    });
    expect(classifyAckDelivery({ error: Object.assign(new Error('aborted'), { name: 'AbortError' }) })).toEqual({
      kind: 'transient',
      reason: 'timeout',
    });
    expect(classifyAckDelivery({ status: 408 })).toEqual({ kind: 'transient', reason: 'http_408' });
    expect(classifyAckDelivery({ status: 429 })).toEqual({ kind: 'transient', reason: 'http_429' });
    expect(classifyAckDelivery({ status: 503 })).toEqual({ kind: 'transient', reason: 'http_5xx' });
    expect(classifyAckDelivery({ status: 200, dropped: 2 })).toEqual({
      kind: 'transient',
      reason: 'backend_dropped',
    });
  });

  it('treats auth, deleted session, and validation 4xx as permanent', () => {
    expect(classifyAckDelivery({ status: 401 })).toEqual({ kind: 'permanent', reason: 'http_401' });
    expect(classifyAckDelivery({ status: 403 })).toEqual({ kind: 'permanent', reason: 'http_403' });
    expect(classifyAckDelivery({ status: 404 })).toEqual({ kind: 'permanent', reason: 'http_404' });
    expect(classifyAckDelivery({ status: 410 })).toEqual({ kind: 'permanent', reason: 'http_410' });
    expect(classifyAckDelivery({ status: 400 })).toEqual({ kind: 'permanent', reason: 'http_4xx' });
    expect(classifyAckDelivery({ status: 422 })).toEqual({ kind: 'permanent', reason: 'validation' });
  });

  it('treats HTTP 200 without dropped as success', () => {
    expect(classifyAckDelivery({ status: 200 })).toEqual({ kind: 'success', reason: 'ok' });
    expect(classifyAckDelivery({ status: 200, dropped: 0 })).toEqual({ kind: 'success', reason: 'ok' });
  });
});

describe('map action ACK follow-up', () => {
  it('delivers a server-issued cartographic repair action to the current session', async () => {
    const body = { repair_action: { action_id: 'ma-carto-1' } };
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    });
    const onResponse = vi.fn();
    let token = 'owner-a';
    const sender = createMapActionAckSender({
      getSessionId: () => 'session-a',
      getToken: () => token,
      debounceMs: 1,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onResponse,
    });

    sender.sink({ action_id: 'ma-original', command: 'add_layer', status: 'succeeded' });
    token = 'owner-b';
    sender.flush();
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0][1].headers['X-Session-Token']).toBe('owner-a');
    expect(onResponse).toHaveBeenCalledWith('session-a', body);
    sender.dispose();
  });

  it('uses the token belonging to the ACK correlation session after a switch', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    const tokens = new Map([
      ['session-a', 'owner-a'],
      ['session-b', 'owner-b'],
    ]);
    const sender = createMapActionAckSender({
      getSessionId: () => 'session-b',
      getToken: (sessionId) => tokens.get(sessionId ?? '') ?? null,
      debounceMs: 1,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    sender.sink({
      action_id: 'ma-cancel-a',
      command: 'cartographic_runtime_repair',
      status: 'cancelled',
      correlation: { session_id: 'session-a' },
    });
    sender.flush();
    await flushMicrotasks();

    expect(fetchImpl.mock.calls[0][0]).toContain('/sessions/session-a/map-action-ack');
    expect(fetchImpl.mock.calls[0][1].headers['X-Session-Token']).toBe('owner-a');
    sender.dispose();
  });
});

describe('map action ACK delivery (fault matrix)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function makeSender(
    fetchImpl: ReturnType<typeof vi.fn>,
    overrides: Partial<Parameters<typeof createMapActionAckSender>[0]> = {},
  ) {
    return createMapActionAckSender({
      getSessionId: () => 'session-a',
      getToken: () => 'owner-a',
      debounceMs: 10,
      retryBaseMs: 100,
      retryMaxMs: 400,
      random: () => 0.5,
      requestTimeoutMs: 0,
      maxAttempts: 3,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      ...overrides,
    });
  }

  it('1. first POST network failure retries with the same action_id and then succeeds', async () => {
    const fetchImpl = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-1'));
    sender.flush();
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const firstBody = JSON.parse(fetchImpl.mock.calls[0][1].body as string);
    const retryBody = JSON.parse(fetchImpl.mock.calls[1][1].body as string);
    expect(retryBody.acks[0].action_id).toBe('ma-1');
    expect(retryBody.acks[0].action_id).toBe(firstBody.acks[0].action_id);
    sender.dispose();
  });

  it('2b. HTTP 200 with dropped>0 retries the same action_id and then succeeds', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ accepted: 0, duplicates: 0, dropped: 1 }))
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-dropped'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchImpl.mock.calls[1][1].body as string).acks[0].action_id).toBe('ma-dropped');
    sender.dispose();
  });

  it('2. HTTP 503 retries and then succeeds', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'unavailable' }, 503))
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-503'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    sender.dispose();
  });

  it('3. HTTP 429 is retried only within the attempt bound', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ detail: 'Too many map action acks' }, 429));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-429'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(200);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(DEFAULT_MAX_ATTEMPTS);
    sender.dispose();
  });

  it('4. HTTP 401/403 are not retried', async () => {
    for (const status of [401, 403]) {
      const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ detail: 'no' }, status));
      const sender = makeSender(fetchImpl);
      sender.sink(ack(`ma-${status}`));
      sender.flush();
      await flushMicrotasks();
      await vi.advanceTimersByTimeAsync(1000);
      await flushMicrotasks();
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      sender.dispose();
    }
  });

  it('4b. HTTP 401 with a rejected json() body is not retried', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.reject(new SyntaxError('<html>unauthorized</html>')),
    });
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-401-html'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    sender.dispose();
  });

  it('5. HTTP 400 validation errors are not retried', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ detail: 'bad ack' }, 400));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-400'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    sender.dispose();
  });

  it('5b. HTTP 400 with a rejected json() body is not retried', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.reject(new SyntaxError('<html>bad request</html>')),
    });
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-400-html'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    sender.dispose();
  });

  it('6. exhausted retries settle the queue and do not loop', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('offline'));
    const warnSpy = vi.spyOn(devOnly, 'warn');
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-ex'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(200);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(5000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(warnSpy.mock.calls.some((c) => String(c[0]).includes('exhausted retry'))).toBe(true);
    warnSpy.mockRestore();
    sender.dispose();
  });

  it('7. a retried POST keeps the same action_id so backend first-terminal-wins sees one identity', async () => {
    const stored = new Set<string>();
    const terminals: string[] = [];
    const fetchImpl = vi.fn()
      .mockRejectedValueOnce(new TypeError('net'))
      .mockImplementation(async (_url: string, init: RequestInit) => {
        const acks = JSON.parse(String(init.body)).acks as Array<{ action_id: string; status: string }>;
        let accepted = 0;
        let duplicates = 0;
        for (const item of acks) {
          if (stored.has(item.action_id)) {
            duplicates += 1;
          } else {
            stored.add(item.action_id);
            terminals.push(item.status);
            accepted += 1;
          }
        }
        return jsonResponse({ accepted, duplicates });
      });
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-dup', { status: 'succeeded' }));
    sender.flush();
    await flushMicrotasks();
    sender.sink(ack('ma-dup', { status: 'failed', error: 'late' }));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();

    expect(stored.size).toBe(1);
    expect(terminals).toEqual(['succeeded']);
    sender.dispose();
  });

  it('8. more than 50 ACKs stay chunked at 50', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ accepted: 50, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    for (let i = 0; i < 51; i += 1) {
      sender.sink(ack(`ma-${i}`));
    }
    sender.flush();
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const sizes = fetchImpl.mock.calls.map((call) => JSON.parse(call[1].body as string).acks.length);
    expect(sizes).toEqual([50, 1]);
    sender.dispose();
  });

  it('9. session A items still POST only to A after a switch to B', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    let session = 'session-a';
    const tokens: Record<string, string> = { 'session-a': 'owner-a', 'session-b': 'owner-b' };
    const sender = createMapActionAckSender({
      getSessionId: () => session,
      getToken: (sid) => tokens[sid ?? session] ?? null,
      debounceMs: 10,
      requestTimeoutMs: 0,
      random: () => 0.5,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    sender.sink(ack('ma-a'));
    session = 'session-b';
    sender.sink(ack('ma-b'));
    sender.flush();
    await flushMicrotasks();

    const urls = fetchImpl.mock.calls.map((call) => String(call[0]));
    const headers = fetchImpl.mock.calls.map((call) => call[1].headers['X-Session-Token']);
    const bodies = fetchImpl.mock.calls.map((call) => JSON.parse(call[1].body as string).acks[0].action_id);
    expect(urls.some((u) => u.includes('/sessions/session-a/map-action-ack'))).toBe(true);
    expect(urls.some((u) => u.includes('/sessions/session-b/map-action-ack'))).toBe(true);
    expect(headers).toContain('owner-a');
    expect(headers).toContain('owner-b');
    const aCall = fetchImpl.mock.calls.find((call) => String(call[0]).includes('/sessions/session-a/'));
    expect(aCall?.[1].headers['X-Session-Token']).toBe('owner-a');
    expect(JSON.parse(aCall?.[1].body as string).acks[0].action_id).toBe('ma-a');
    expect(bodies).toEqual(expect.arrayContaining(['ma-a', 'ma-b']));
    expect(String(aCall?.[0])).not.toMatch(/owner-a|token=/i);
    sender.dispose();
  });

  it('dispose does not abort an in-flight flush (unmount tail POST)', async () => {
    let capturedSignal: AbortSignal | undefined;
    let release: ((value: unknown) => void) | undefined;
    const fetchImpl = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      capturedSignal = init.signal;
      return new Promise((resolve) => {
        release = resolve;
      });
    });
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-tail'));
    sender.flush();
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(capturedSignal?.aborted).toBe(false);

    sender.dispose();
    expect(capturedSignal?.aborted).toBe(false);

    release?.(jsonResponse({ accepted: 1, duplicates: 0 }));
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();

    expect(capturedSignal?.aborted).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('10. dispose cancels retry timers so advancing time does not POST again', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('offline'));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-unmount'));
    sender.flush();
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    sender.dispose();
    await vi.advanceTimersByTimeAsync(10_000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('dispose then a new sink still delivers (Strict Mode remount)', async () => {
    const fetchImpl = vi.fn()
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-old'));
    sender.flush();
    await flushMicrotasks();
    sender.dispose();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    sender.sink(ack('ma-new'));
    sender.flush();
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchImpl.mock.calls[1][1].body as string).acks[0].action_id).toBe('ma-new');
    sender.dispose();
  });

  it('HTTP 200 with an unreadable JSON body is transient and retries', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.reject(new SyntaxError('not json')),
      })
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-html'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    sender.dispose();
  });

  it('a hung response body is aborted by the request timeout and leaves inFlight', async () => {
    const fetchImpl = vi.fn()
      .mockImplementationOnce((_url: string, init: RequestInit) => Promise.resolve({
        ok: true,
        status: 200,
        json: () => new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => {
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
          });
        }),
      }))
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl, { requestTimeoutMs: 50 });

    sender.sink(ack('ma-hang-body'));
    sender.flush();
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(50);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(100);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    sender.dispose();
  });

  it('a skipped stale-session repair does not burn the repair id', async () => {
    const repair = { action_id: 'ma-carto-1', command: 'cartographic_runtime_repair', params: {} };
    const onResponse = vi.fn(() => false);
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({
      accepted: 1,
      duplicates: 0,
      repair_action: repair,
    }));
    const sender = makeSender(fetchImpl, { onResponse });

    sender.sink(ack('ma-orig-1'));
    sender.flush();
    await flushMicrotasks();
    sender.sink(ack('ma-orig-2'));
    sender.flush();
    await flushMicrotasks();

    expect(onResponse).toHaveBeenCalledTimes(2);
    sender.dispose();
  });

  it('onResponse throw after HTTP 200 does not retry the settled batch', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({
      accepted: 1,
      duplicates: 0,
      repair_action: { action_id: 'ma-carto-boom', command: 'cartographic_runtime_repair', params: {} },
    }));
    const sender = makeSender(fetchImpl, {
      onResponse: () => {
        throw new Error('dispatch exploded');
      },
    });

    sender.sink(ack('ma-ok'));
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(1000);
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    sender.dispose();
  });

  it('11. a duplicate successful repair_action is delivered to onResponse only once', async () => {
    const repair = { action_id: 'ma-carto-1', command: 'cartographic_runtime_repair', params: {} };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({
      accepted: 1,
      duplicates: 0,
      repair_action: repair,
    }));
    const onResponse = vi.fn();
    const sender = makeSender(fetchImpl, { onResponse });

    sender.sink(ack('ma-orig-1'));
    sender.flush();
    await flushMicrotasks();
    sender.sink(ack('ma-orig-2'));
    sender.flush();
    await flushMicrotasks();

    const repairCalls = onResponse.mock.calls.filter((c) => {
      const body = c[1] as { repair_action?: { action_id?: string } };
      return body?.repair_action?.action_id === 'ma-carto-1';
    });
    expect(repairCalls).toHaveLength(1);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    sender.dispose();
  });

  it('12. a slow in-flight retry does not block a new ACK batch from enqueueing and POSTing', async () => {
    let releaseFirst: ((value: unknown) => void) | undefined;
    const fetchImpl = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => {
        releaseFirst = resolve;
      }))
      .mockResolvedValue(jsonResponse({ accepted: 1, duplicates: 0 }));
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-slow'));
    sender.flush();
    await flushMicrotasks();
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    sender.sink(ack('ma-new'));
    sender.flush();
    await flushMicrotasks();

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const secondIds = JSON.parse(fetchImpl.mock.calls[1][1].body as string).acks.map(
      (item: { action_id: string }) => item.action_id,
    );
    expect(secondIds).toEqual(['ma-new']);
    releaseFirst?.(jsonResponse({ accepted: 1, duplicates: 0 }));
    sender.dispose();
  });

  it('13. the in-memory queue stays bounded under a sustained outage', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('offline'));
    const warnSpy = vi.spyOn(devOnly, 'warn');
    const sender = makeSender(fetchImpl, { maxQueue: 8, maxAttempts: 2, debounceMs: 0 });

    for (let i = 0; i < 20; i += 1) {
      sender.sink(ack(`ma-flood-${i}`));
    }
    sender.flush();
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(500);
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(500);
    await flushMicrotasks();

    expect(warnSpy.mock.calls.some((c) => String(c[0]).includes('queue overflow'))).toBe(true);
    const posted = fetchImpl.mock.calls.reduce((sum, call) => {
      return sum + JSON.parse(call[1].body as string).acks.length;
    }, 0);
    expect(posted).toBeLessThanOrEqual(8 * 2);
    expect(DEFAULT_MAX_QUEUE).toBeGreaterThan(50);
    warnSpy.mockRestore();
    sender.dispose();
  });

  it('does not put the owner token in the URL, and logs never include it or the body', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('offline'));
    const warnSpy = vi.spyOn(devOnly, 'warn');
    const sender = makeSender(fetchImpl);

    sender.sink(ack('ma-sec', {
      requested: { geojson: { type: 'FeatureCollection', features: [] } },
    }));
    sender.flush();
    await flushMicrotasks();

    expect(String(fetchImpl.mock.calls[0][0])).not.toMatch(/owner-a|X-Session-Token/i);
    const logText = JSON.stringify(warnSpy.mock.calls);
    expect(logText).not.toContain('owner-a');
    expect(logText).not.toMatch(/FeatureCollection|geojson/i);
    warnSpy.mockRestore();
    sender.dispose();
  });
});
