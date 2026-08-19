import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { ApiError } from '@/lib/api/transport';
import { useWorkspaceSession } from './use-workspace-session';
import { devOnly } from '@/lib/utils/logger';
import { resetViewportSeq } from '@/lib/utils/viewport-seq';

// ── Mocks ────────────────────────────────────────────────────────────────
// The hook both subscribes (useHudStore(selector)) and reads transient state
// (useHudStore.getState()) — the mock must support both call styles.
const hudState = vi.hoisted(() => ({
  clearLayers: vi.fn(),
  clearOpsLog: vi.fn(),
  clearCausalChain: vi.fn(),
  clearAnnotations: vi.fn(),
  setSessions: vi.fn(),
  setSelectedFeature: vi.fn(),
  setAiStatus: vi.fn(),
  clearTask: vi.fn(),
  clearExplorerTasks: vi.fn(),
  setBaseLayer: vi.fn(),
  addLayer: vi.fn(),
  fetchAnalysisAssets: vi.fn().mockResolvedValue(undefined),
  clearResults: vi.fn(),
  clearProcessLayers: vi.fn(),
  setCartographyTitle: vi.fn(),
  focusLayer: vi.fn(),
  historyOpen: false,
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (selector: (s: typeof hudState) => unknown) => selector(hudState),
    { getState: () => hudState }
  ),
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8000',
}));

// ── Response doubles (same shape transport.test.ts uses) ─────────────────
const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  json: () => Promise.resolve(body), // refreshSessions still uses raw fetch
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, statusText: string, body: unknown) => ({
  ok: false,
  status,
  statusText,
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
});

const fetchMock = vi.fn();

describe('useWorkspaceSession selectSession (F-09)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    resetViewportSeq();
    hudState.historyOpen = false;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('non-OK restore surfaces a typed ApiError and resets messages to empty', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] })) // mount: refreshSessions
      .mockResolvedValueOnce(jsonErr(404, 'Not Found', { detail: 'session not found' })); // restore

    const onRestore = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('bad-id', onRestore);
    });

    // typed error surfaced through the hook's error channel (was: silent)
    const errorSpy = vi.mocked(devOnly.error);
    expect(errorSpy).toHaveBeenCalledWith('Load session failed:', expect.any(ApiError));
    const err = errorSpy.mock.calls[0][1] as ApiError;
    expect(err.status).toBe(404);
    expect(err.body).toEqual({ detail: 'session not found' });

    // #392: 失败路径也无条件重置 transcript —— messages 清空 + 附错误提示，
    // 上一会话的聊天不会残留到新会话身份下（was: onRestore 完全不调用）。
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith([], expect.stringContaining('session not found'));
    // 没有 phantom history message，且没有后续恢复工作（map-state /
    // layers / analysis assets）—— 失败的恢复不得再写状态
    expect(fetchMock).toHaveBeenCalledTimes(2); // sessions list + restore only
    expect(fetchMock.mock.calls[1][0]).toContain('/api/v1/chat/sessions/bad-id');
    expect(hudState.fetchAnalysisAssets).not.toHaveBeenCalled();
  });

  it('non-JSON error body surfaces as ApiError instead of a swallowed SyntaxError', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonErr(502, 'Bad Gateway', '<html>proxy error</html>'));

    const onRestore = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('proxy-down', onRestore);
    });

    const errorSpy = vi.mocked(devOnly.error);
    expect(errorSpy).toHaveBeenCalledWith('Load session failed:', expect.any(ApiError));
    const err = errorSpy.mock.calls[0][1] as ApiError;
    expect(err.status).toBe(502);
    expect(err.body).toBe('<html>proxy error</html>');
    // #392: 非 JSON 错误体 -> messages 同样被重置（detail 不可用时回退 HTTP 状态）
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith([], expect.stringContaining('HTTP 502'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('successful restore still applies messages and map state (regression guard)', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(
        jsonOk({
          title: '历史会话',
          messages: [{ id: 'm1', role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00Z' }],
        })
      )
      .mockResolvedValueOnce(
        jsonOk({
          map_state: {
            viewport: { center: [116, 39], zoom: 12 },
            _viewport_seq: 1,
            base_layer: 'streets',
            layers: [],
          },
        })
      );

    const onRestore = vi.fn();
    const dispatchAction = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(dispatchAction));
    await act(async () => {
      await result.current.selectSession('sid-1', onRestore);
    });

    expect(onRestore).toHaveBeenCalledTimes(1);
    const restored = onRestore.mock.calls[0][0] as Array<Record<string, unknown>>;
    expect(restored[0]).toMatchObject({ id: 'm1', role: 'user', content: 'hello' });
    expect(restored[1]?.role).toBe('assistant');
    expect(restored[1]?.content).toContain('已恢复历史会话');

    // Viewport hints are Observed, not Desired — reload must not snap
    // the camera unless MapSpec.view was an explicit frame (#640).
    expect(dispatchAction).not.toHaveBeenCalledWith(
      expect.objectContaining({ command: 'fly_to' }),
    );
    expect(hudState.setBaseLayer).toHaveBeenCalledWith('streets');
    expect(hudState.fetchAnalysisAssets).toHaveBeenCalledWith('sid-1');
  });

  it('snaps the camera on restore only for an explicit MapSpec.view frame', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonOk({ title: 'Map', messages: [] }))
      .mockResolvedValueOnce(jsonOk({
        map_state: {
          viewport: { center: [1, 2], zoom: 4 },
          mapspec: { view: { center: [114.3, 30.5], zoom: 10, framed: true } },
          layers: [],
        },
      }));

    const onRestore = vi.fn();
    const dispatchAction = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(dispatchAction));
    await act(async () => {
      await result.current.selectSession('sid-2', onRestore);
    });

    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'fly_to',
      params: expect.objectContaining({ center: [114.3, 30.5], zoom: 10 }),
    });
  });

  it('restores the final runtime result from cartographic observation metadata', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonOk({ title: 'Map', messages: [] }))
      .mockResolvedValueOnce(jsonOk({
        map_state: {
          layers: [],
          _current_cartographic_fingerprint: 'carto-sha256:current',
          _cartographic_observation: {
            mapspec_fingerprint: 'carto-sha256:current',
            layers: [{
              id: 'result',
              runtime_store_id: 'ref:geojson-final',
              name: 'Final result',
              type: 'vector',
              visible: true,
              opacity: 0.8,
              _refId: 'ref:geojson-final',
              _descriptor: {
                ref_id: 'ref:geojson-final',
                feature_count: 9000,
                point_count: 9000,
                geometry_types: ['Point'],
                bbox: [100, 20, 101, 21],
                mvt_capable: true,
                estimated_bytes: 900000,
              },
              projection_fingerprint: 'runtime-sha256:final',
              intent_generation: 17,
            }],
          },
        },
      }));

    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('sid-final', vi.fn());
    });

    expect(hudState.addLayer).toHaveBeenCalledWith(expect.objectContaining({
      id: 'ref:geojson-final',
      _refId: 'ref:geojson-final',
      _mapspecLayerId: 'result',
      _mapspecFingerprint: 'carto-sha256:current',
      _mapspecProjectionFingerprint: 'runtime-sha256:final',
      _intentGeneration: 17,
      visible: true,
    }));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('does not restore a runtime observation from an older MapSpec generation', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonOk({ title: 'Map', messages: [] }))
      .mockResolvedValueOnce(jsonOk({
        map_state: {
          layers: [{ id: 'authoritative-layer', name: 'Current', type: 'vector' }],
          _current_cartographic_fingerprint: 'carto-sha256:new',
          _cartographic_observation: {
            mapspec_fingerprint: 'carto-sha256:old',
            layers: [{
              id: 'stale-result',
              runtime_store_id: 'ref:geojson-stale',
              _refId: 'ref:geojson-stale',
            }],
          },
        },
      }));

    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('sid-stale', vi.fn());
    });

    expect(hudState.addLayer).toHaveBeenCalledWith(expect.objectContaining({
      id: 'authoritative-layer',
    }));
    expect(hudState.addLayer).not.toHaveBeenCalledWith(expect.objectContaining({
      id: 'ref:geojson-stale',
    }));
  });

  it('abort during restore is not surfaced as an error (AbortError contract)', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockRejectedValueOnce(new DOMException('The user aborted a request.', 'AbortError'));

    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('sid-1', vi.fn());
    });

    expect(vi.mocked(devOnly.error)).not.toHaveBeenCalled();
  });

  it('retains anonymous owner tokens per session during A/B switching', async () => {
    fetchMock.mockResolvedValue(jsonOk({ sessions: [], messages: [], map_state: null }));
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    act(() => {
      result.current.rememberSessionToken('session-a', 'token-a');
      result.current.rememberSessionToken('session-b', 'token-b');
    });

    await act(async () => {
      await result.current.selectSession('session-a', vi.fn());
    });
    const aRestore = fetchMock.mock.calls.find(
      ([url]) => String(url).endsWith('/sessions/session-a'),
    );
    expect(aRestore?.[1]?.headers?.['X-Session-Token']).toBe('token-a');

    await act(async () => {
      await result.current.selectSession('session-b', vi.fn());
    });
    const bRestore = fetchMock.mock.calls.find(
      ([url]) => String(url).endsWith('/sessions/session-b'),
    );
    expect(bRestore?.[1]?.headers?.['X-Session-Token']).toBe('token-b');
    expect(result.current.sessionTokenRef.current).toBe('token-b');
  });

  it('bounds retained anonymous owner capabilities', () => {
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    act(() => {
      for (let index = 0; index < 129; index += 1) {
        result.current.rememberSessionToken(`session-${index}`, `token-${index}`);
      }
    });

    expect(result.current.getSessionTokenFor('session-0')).toBeNull();
    expect(result.current.getSessionTokenFor('session-1')).toBe('token-1');
    expect(result.current.getSessionTokenFor('session-128')).toBe('token-128');
  });

  it('F-1: startNewSession aborts an in-flight selectSession restore', async () => {
    // A slow restore for session A resolving AFTER the user started a new
    // session must not mutate the fresh session (no onRestoreMessages).
    let resolveRestore: (v: unknown) => void = () => {};
    fetchMock.mockImplementation(() => new Promise((r) => { resolveRestore = r; }));
    const onRestore = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));

    let restorePromise!: Promise<unknown>;
    act(() => {
      restorePromise = result.current.selectSession('session-a', onRestore);
    });
    // New session while the restore fetch is still pending -> aborts it.
    act(() => {
      result.current.startNewSession(vi.fn());
    });
    await act(async () => {
      resolveRestore(jsonOk({
        messages: [{ id: 'm1', role: 'assistant', content: 'A', timestamp: '2026-01-01T00:00:00Z' }],
        map_state: null,
      }));
      await restorePromise.catch(() => undefined);
    });

    expect(onRestore).not.toHaveBeenCalled();
    // F-4: the result registry must also be cleared for the fresh session.
    expect(vi.mocked(hudState.clearResults)).toHaveBeenCalled();
  });

  it('#548: startNewSession clears explorer task cards', () => {
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    act(() => {
      result.current.startNewSession(vi.fn());
    });
    expect(vi.mocked(hudState.clearExplorerTasks)).toHaveBeenCalled();
  });

  it('#618: selectSession clears leftover session-scoped HUD fields', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonOk({ title: 'S', messages: [] }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }));

    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('sid-leftover', vi.fn());
    });

    expect(vi.mocked(hudState.clearOpsLog)).toHaveBeenCalled();
    expect(vi.mocked(hudState.clearCausalChain)).toHaveBeenCalled();
    expect(vi.mocked(hudState.clearProcessLayers)).toHaveBeenCalled();
    expect(vi.mocked(hudState.focusLayer)).toHaveBeenCalledWith(null);
    expect(vi.mocked(hudState.setCartographyTitle)).toHaveBeenCalledWith(null);
  });

  it('#618: startNewSession also clears cartography leftovers', () => {
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    act(() => {
      result.current.startNewSession(vi.fn());
    });
    expect(vi.mocked(hudState.clearOpsLog)).toHaveBeenCalled();
    expect(vi.mocked(hudState.clearCausalChain)).toHaveBeenCalled();
    expect(vi.mocked(hudState.clearProcessLayers)).toHaveBeenCalled();
    expect(vi.mocked(hudState.focusLayer)).toHaveBeenCalledWith(null);
    expect(vi.mocked(hudState.setCartographyTitle)).toHaveBeenCalledWith(null);
  });

  it('#548: selectSession clears explorer task cards on session switch', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonOk({ title: 'S', messages: [] }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }));

    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('sid-explorer', vi.fn());
    });
    expect(vi.mocked(hudState.clearExplorerTasks)).toHaveBeenCalled();
  });

  it('#392: History 抽屉打开信号触发 refreshSessions（不再只 mount 拉一次）', async () => {
    fetchMock.mockResolvedValue(jsonOk({ sessions: [{ id: 's1', title: '会话一' }] }));
    const { rerender } = renderHook(() => useWorkspaceSession(vi.fn()));
    // mount 拉取 settle
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // 模拟 store 的 historyOpen 翻转为 true（History 抽屉打开）
    act(() => {
      hudState.historyOpen = true;
      rerender();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const lastCall = fetchMock.mock.calls[1];
    expect(String(lastCall[0])).toContain('/api/v1/chat/sessions');
  });

  it('#392: 空会话恢复时无条件把 transcript 重置为空', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonOk({ title: '空会话', messages: [] }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }));

    const onRestore = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('empty-session', onRestore);
    });

    // was: messages 为空时 onRestoreMessages 不被调用，上一会话的聊天
    // 残留屏幕（sessionIdRef 已指向新会话）
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith([]);
  });

  it('#392: 恢复失败时 messages 被重置为空并附错误提示（不静默）', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ sessions: [] }))
      .mockResolvedValueOnce(jsonErr(500, 'Internal Server Error', { detail: 'db down' }));

    const onRestore = vi.fn();
    const { result } = renderHook(() => useWorkspaceSession(vi.fn()));
    await act(async () => {
      await result.current.selectSession('broken', onRestore);
    });

    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith([], expect.stringContaining('db down'));
    // 错误提示里明确说明恢复失败，而不是静默
    const notice = onRestore.mock.calls[0][1] as string;
    expect(notice).toContain('加载会话失败');
    expect(notice).toContain('历史记录未恢复');
  });
});
