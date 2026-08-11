import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiFetch, ApiError, ApiTimeoutError, isIdempotentMethod } from './transport';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

/** Minimal success response double with a JSON body. */
const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  text: () => Promise.resolve(JSON.stringify(body)),
});

/** Error response double; body undefined → empty body. */
const errResponse = (status: number, statusText = '', body?: unknown) => ({
  ok: false,
  status,
  statusText,
  text: () =>
    Promise.resolve(
      body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body)
    ),
});

describe('transport (F-FE-3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('typed errors (status + body + request id)', () => {
    it('throws ApiError carrying status, parsed body, and echoed request id', async () => {
      mockFetch.mockResolvedValueOnce({
        ...errResponse(404, 'Not Found', { detail: 'session not found' }),
        headers: { get: (name: string) => (name.toLowerCase() === 'x-request-id' ? 'srv-echo-1' : null) },
      });

      const err = await apiFetch('/sessions/nope').catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(404);
      expect(apiErr.body).toEqual({ detail: 'session not found' });
      expect(apiErr.requestId).toBe('srv-echo-1');
      expect(apiErr.retryable).toBe(false);
      expect(apiErr.message).toContain('404');
    });

    it('falls back to the generated request id when the server does not echo', async () => {
      mockFetch.mockResolvedValueOnce(errResponse(500, 'Internal Server Error'));
      const err = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
      expect(err.requestId).toMatch(/^[0-9a-f-]{36}$/);
    });

    it('keeps non-JSON error bodies as raw text', async () => {
      mockFetch.mockResolvedValueOnce(errResponse(502, 'Bad Gateway', '<html>proxy error</html>'));
      const err = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
      expect(err.body).toBe('<html>proxy error</html>');
    });

    it('marks 5xx retryable and 4xx not', async () => {
      mockFetch.mockResolvedValueOnce(errResponse(503, 'Service Unavailable'));
      const e5 = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
      expect(e5.retryable).toBe(true);

      mockFetch.mockResolvedValueOnce(errResponse(400, 'Bad Request'));
      const e4 = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
      expect(e4.retryable).toBe(false);
    });
  });

  describe('timeout model (AbortController-based)', () => {
    it('aborts with ApiTimeoutError when the server never responds', async () => {
      let capturedSignal: AbortSignal | undefined;
      mockFetch.mockImplementation(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            capturedSignal = init?.signal ?? undefined;
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('The operation timed out.', 'TimeoutError'));
            });
          })
      );

      const err = await apiFetch('/slow', { timeoutMs: 25 }).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiTimeoutError);
      const timeoutErr = err as ApiTimeoutError;
      expect(timeoutErr.timeoutMs).toBe(25);
      expect(timeoutErr.requestId).toMatch(/^[0-9a-f-]{36}$/);
      // The AbortController actually fired — the fetch signal was aborted.
      expect(capturedSignal?.aborted).toBe(true);
    });

    it('surfaces a caller abort as AbortError, not a timeout', async () => {
      const controller = new AbortController();
      mockFetch.mockImplementation(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('The user aborted a request.', 'AbortError'));
            });
          })
      );

      const pending = apiFetch('/x', { signal: controller.signal, timeoutMs: 10_000 });
      controller.abort();
      await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    });
  });

  describe('retry safety (non-idempotent POST is never re-sent)', () => {
    it('never retries a POST even when retries are requested', async () => {
      mockFetch.mockResolvedValue(errResponse(503));
      await expect(apiFetch('/chat/completions', { method: 'POST', retries: 3 })).rejects.toBeInstanceOf(
        ApiError
      );
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    it('never retries executeToolDirect-style POSTs', async () => {
      mockFetch.mockResolvedValue(errResponse(500));
      await expect(
        apiFetch('/chat/tools/execute', { method: 'POST', body: { tool: 'x' }, retries: 2 })
      ).rejects.toBeInstanceOf(ApiError);
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    it('retries an idempotent GET on a transient 5xx when retries are requested', async () => {
      mockFetch
        .mockResolvedValueOnce(errResponse(503))
        .mockResolvedValueOnce(jsonOk({ items: [1] }));
      const result = await apiFetch<{ items: number[] }>('/sessions', {
        retries: 1,
        retryDelayMs: 0,
      });
      expect(mockFetch).toHaveBeenCalledTimes(2);
      expect(result).toEqual({ items: [1] });
    });

    it('retries an idempotent GET on a network error when retries are requested', async () => {
      mockFetch
        .mockRejectedValueOnce(new TypeError('Failed to fetch'))
        .mockResolvedValueOnce(jsonOk({}));
      const result = await apiFetch('/x', { retries: 1, retryDelayMs: 0 });
      expect(mockFetch).toHaveBeenCalledTimes(2);
      expect(result).toEqual({});
    });

    it('does not retry 4xx even for idempotent GETs', async () => {
      mockFetch.mockResolvedValue(errResponse(404, 'Not Found'));
      await expect(apiFetch('/x', { retries: 2, retryDelayMs: 0 })).rejects.toBeInstanceOf(ApiError);
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    it('classifies methods for retry safety', () => {
      expect(isIdempotentMethod('GET')).toBe(true);
      expect(isIdempotentMethod('DELETE')).toBe(true);
      expect(isIdempotentMethod('post')).toBe(false);
      expect(isIdempotentMethod('PATCH')).toBe(false);
    });
  });

  describe('request-id propagation', () => {
    it('sends an auto-generated X-Request-ID header on every request', async () => {
      mockFetch.mockResolvedValueOnce(jsonOk({}));
      await apiFetch('/x');
      const headers = mockFetch.mock.calls[0][1].headers as Record<string, string>;
      expect(headers['X-Request-ID']).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
      );
    });

    it('honors an explicit requestId override', async () => {
      mockFetch.mockResolvedValueOnce(jsonOk({}));
      await apiFetch('/x', { requestId: 'turn-abc' });
      const headers = mockFetch.mock.calls[0][1].headers as Record<string, string>;
      expect(headers['X-Request-ID']).toBe('turn-abc');
    });
  });

  describe('body handling', () => {
    it('serializes JSON bodies and sets Content-Type', async () => {
      mockFetch.mockResolvedValueOnce(jsonOk({}));
      await apiFetch('/chat', { method: 'POST', body: { message: 'hi', n: 1 } });
      const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
      expect(url).toContain('/chat');
      expect(init.method).toBe('POST');
      expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
      expect(JSON.parse(init.body as string)).toEqual({ message: 'hi', n: 1 });
    });

    it('resolves undefined for 204 No Content', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, status: 204, statusText: 'No Content' });
      await expect(apiFetch('/x')).resolves.toBeUndefined();
    });

    it('resolves undefined for an empty success body', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, status: 200, statusText: 'OK', text: () => Promise.resolve('') });
      await expect(apiFetch('/x')).resolves.toBeUndefined();
    });

    it('resolves undefined without reading the body when parseJson is false', async () => {
      // No text() on purpose — would throw if the transport tried to read it.
      mockFetch.mockResolvedValueOnce({ ok: true, status: 200, statusText: 'OK' });
      await expect(apiFetch('/x', { parseJson: false })).resolves.toBeUndefined();
    });
  });
});
