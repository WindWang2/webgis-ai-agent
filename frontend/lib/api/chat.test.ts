import { describe, it, expect, vi, beforeEach } from 'vitest';
import { sendChat, getSessionList, deleteSession, executeToolDirect, streamChat } from './chat';
import type { SSEEvent } from './chat';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

describe('Chat API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('sendChat', () => {
    it('makes POST to correct endpoint', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({ content: 'hi', session_id: 's1' })),
      });
      const result = await sendChat('hello');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/chat/completions'),
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
          body: expect.stringContaining('"message":"hello"'),
        })
      );
      expect(result).toEqual({ content: 'hi', session_id: 's1' });
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
      await expect(sendChat('hello')).rejects.toThrow('Chat API error: 500');
    });

    it('includes sessionId in request body', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({ content: 'hi', session_id: 's1' })),
      });
      await sendChat('hello', 'sess-123');
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.session_id).toBe('sess-123');
    });
  });

  describe('getSessionList', () => {
    it('fetches sessions from correct endpoint', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify([{ id: 's1' }])),
      });
      const result = await getSessionList();
      // transport 现在总是带 init（含 X-Request-ID 头），因此断言第二个参数。
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/chat/sessions'),
        expect.objectContaining({ method: 'GET' })
      );
      expect(result).toEqual([{ id: 's1' }]);
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 404 });
      await expect(getSessionList()).rejects.toThrow('API Error: 404');
    });
  });

  describe('deleteSession', () => {
    it('sends DELETE request', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true });
      await deleteSession('s1');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/chat/sessions/s1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 403 });
      await expect(deleteSession('s1')).rejects.toThrow('API Error: 403');
    });

    // SEC-08: owner_token (when provided) is sent via X-Session-Token header.
    it('includes X-Session-Token header when ownerToken is provided', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true });
      await deleteSession('s1', 'tok-123');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/chat/sessions/s1'),
        expect.objectContaining({
          method: 'DELETE',
          headers: expect.objectContaining({ 'X-Session-Token': 'tok-123' }),
        })
      );
    });

    it('omits X-Session-Token header when ownerToken is absent', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true });
      await deleteSession('s1');
      const callArgs = mockFetch.mock.calls[0][1];
      // F-FE-3: transport 现在总是注入 X-Request-ID 头，因此这里只断言不携带
      // X-Session-Token（而不是 headers 为空对象）。
      expect(callArgs.headers).toEqual({ 'X-Request-ID': expect.any(String) });
      expect(callArgs.headers['X-Session-Token']).toBeUndefined();
    });
  });

  // FE-14: clearSessionMessages removed (was identical to deleteSession, backend has no /clear route)
  // Tests for deleteSession already cover the DELETE /sessions/{id} contract.

  describe('executeToolDirect', () => {
    it('sends POST with tool and arguments (复数，匹配后端 ToolExecuteRequest)', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({ type: 'result' })),
      });
      const result = await executeToolDirect('query_osm_poi', { query: 'school' });
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/chat/tools/execute'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"tool":"query_osm_poi"'),
        })
      );
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      // 审计：字段名必须是 arguments（复数）—— 之前发 argument（单数）会被
      // pydantic 默认值 {} 覆盖，工具收到空参数。
      expect(body.arguments).toEqual({ query: 'school' });
      expect(body.argument).toBeUndefined();
      expect(result).toEqual({ type: 'result' });
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
      await expect(executeToolDirect('bad_tool', {})).rejects.toThrow('Tool execute error: 500');
    });
  });

  describe('streamChat', () => {
    function makeSSEStream(events: string[]): Response {
      const body = events.join('\n') + '\n';
      return {
        ok: true,
        body: {
          getReader: () => {
            let sent = false;
            return {
              read: async () => {
                if (!sent) {
                  sent = true;
                  return { done: false, value: new TextEncoder().encode(body) };
                }
                return { done: true, value: undefined };
              },
              cancel: vi.fn(),
            };
          },
        },
      } as unknown as Response;
    }

    it('yields parsed SSEEvents from well-formed SSE stream', async () => {
      mockFetch.mockResolvedValueOnce(makeSSEStream([
        'event: thinking',
        'data: {"content":"..."}',
        '',
        'event: done',
        'data: {}',
        '',
      ]));

      const events: SSEEvent[] = [];
      for await (const e of streamChat('hello')) {
        events.push(e);
      }
      expect(events).toHaveLength(2);
      expect(events[0].event).toBe('thinking');
      expect(events[1].event).toBe('done');
    });

    it('yields raw string on JSON parse failure', async () => {
      mockFetch.mockResolvedValueOnce(makeSSEStream([
        'event: content',
        'data: NOT_VALID_JSON',
        '',
      ]));

      const events: SSEEvent[] = [];
      for await (const e of streamChat('hello')) {
        events.push(e);
      }
      expect(events[0].data).toBe('NOT_VALID_JSON');
    });

    it('stops yielding when AbortSignal is aborted mid-stream', async () => {
      const controller = new AbortController();
      let readCallCount = 0;
      mockFetch.mockResolvedValueOnce({
        ok: true,
        body: {
          getReader: () => ({
            read: async () => {
              readCallCount++;
              if (readCallCount === 1) {
                controller.abort();
                return { done: false, value: new TextEncoder().encode('event: thinking\ndata: {}\n\n') };
              }
              return { done: true, value: undefined };
            },
            cancel: vi.fn(),
          }),
        },
      } as unknown as Response);

      const events: SSEEvent[] = [];
      for await (const e of streamChat('hello', undefined, undefined, controller.signal)) {
        events.push(e);
      }
      expect(events.length).toBeLessThanOrEqual(1);
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 503 });
      const gen = streamChat('hello');
      await expect(gen.next()).rejects.toThrow('Chat API error: 503');
    });

    it('sends session_id and map_state in request body', async () => {
      mockFetch.mockResolvedValueOnce(makeSSEStream([]));
      for await (const _ of streamChat('hello', 'sess-1', { zoom: 10 })) { /* drain */ }
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.session_id).toBe('sess-1');
      expect(body.map_state).toEqual({ zoom: 10 });
    });
  });
});
