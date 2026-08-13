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
  setBaseLayer: vi.fn(),
  addLayer: vi.fn(),
  fetchAnalysisAssets: vi.fn().mockResolvedValue(undefined),
  clearResults: vi.fn(),
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
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('non-OK restore surfaces a typed ApiError and does not clobber state', async () => {
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

    // no phantom history message, and no further restore work (map-state /
    // layers / analysis assets) — a failed restore must not write state
    expect(onRestore).not.toHaveBeenCalled();
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
    expect(onRestore).not.toHaveBeenCalled();
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

    // map-state viewport still applied (F4 coalesce passes seq 1) + base layer
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'fly_to',
      params: expect.objectContaining({ center: [116, 39], zoom: 12 }),
    });
    expect(hudState.setBaseLayer).toHaveBeenCalledWith('streets');
    expect(hudState.fetchAnalysisAssets).toHaveBeenCalledWith('sid-1');
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
});
