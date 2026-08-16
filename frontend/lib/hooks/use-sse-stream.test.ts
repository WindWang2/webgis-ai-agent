import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  useSSEStream,
  buildSelectedFeatureSnapshot,
  resolveParentLayerId,
} from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import type { SelectedFeatureInfo, ToolCallEntry } from '@/lib/store/hud-types';

// ── Mocks ────────────────────────────────────────────────────────────────
// use-sse-stream consumes the bridge for aiStatus + send; we spy on send to
// capture the mapState payload (the FE-4 contract under test). The mock also
// captures the real onEvent callback (useMapBridge's 3rd arg —
// (sessionId, dispatchAction, onEvent, sessionTokenRef, opts)) so tests can
// drive SSE events straight into the hook.
const bridgeMock = vi.hoisted(() => ({
  send: vi.fn().mockResolvedValue(undefined),
  aiStatus: 'idle',
  onEventCallback: null as ((event: {
    event: string;
    data: Record<string, unknown>;
  }) => void) | null,
}));

vi.mock('./useMapBridge', () => ({
  useMapBridge: (...args: unknown[]) => {
    bridgeMock.onEventCallback = args[2] as typeof bridgeMock.onEventCallback;
    return bridgeMock;
  },
}));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const setSessionId = vi.fn();
const dispatchAction = vi.fn();
const getMapSnapshot = vi.fn(() => null);

function renderStream() {
  return renderHook(() =>
    useSSEStream(
      'sid-fe4',
      setSessionId,
      { current: 'sid-fe4' },
      dispatchAction,
      getMapSnapshot,
      null,
      { current: null },
    ),
  );
}

async function sendAndGetMapState() {
  const { result } = renderStream();
  await act(async () => {
    await result.current.handleSend('hi');
  });
  return bridgeMock.send.mock.calls[0][1] as Record<string, unknown>;
}

describe('resolveParentLayerId (FE-4 design §7)', () => {
  it('longest-prefix match fixes poi vs poi_schools mis-attribution', () => {
    expect(resolveParentLayerId('poi_schools__fill', ['poi', 'poi_schools'])).toBe('poi_schools');
    expect(resolveParentLayerId('poi__point', ['poi', 'poi_schools'])).toBe('poi');
  });

  it('matches ref/custom parent ids and falls back to stripping __sub', () => {
    expect(resolveParentLayerId('ref:geojson-abc__point', ['ref:geojson-abc'])).toBe(
      'ref:geojson-abc',
    );
    // parent layer gone from the project → strip the sublayer suffix, never
    // emit the raw `__` id into the prompt path
    expect(resolveParentLayerId('poi_schools__fill', [])).toBe('poi_schools');
    expect(resolveParentLayerId('ref:x__point', [])).toBe('ref:x');
    // already a parent id / unknown ids pass through unchanged
    expect(resolveParentLayerId('poi_schools', ['poi', 'poi_schools'])).toBe('poi_schools');
    expect(resolveParentLayerId('custom-9', [])).toBe('custom-9');
    expect(resolveParentLayerId('', ['a'])).toBe('');
  });
});

describe('buildSelectedFeatureSnapshot (FE-4 design §7)', () => {
  const base: SelectedFeatureInfo = {
    layerId: 'ref:geojson-abc__point',
    layerName: '测试层',
    refId: 'ref:geojson-abc',
    point: [116.4, 39.9],
    properties: {},
    selectedAt: 1700000000000,
  };

  it('bounds properties to ≤5 scalar entries, dropping nested payloads', () => {
    const sel: SelectedFeatureInfo = {
      ...base,
      properties: {
        name: '第一区',
        area_km2: 12.5,
        geometry: { type: 'Polygon', coordinates: [[[116.3, 39.8]]] },
        tags: { a: 1 },
        long_val: 'x'.repeat(5000),
        extra1: 'v1',
        extra2: 'v2',
        extra3: 'v3',
      },
    };
    const snap = buildSelectedFeatureSnapshot(sel, ['ref:geojson-abc']);
    const props = snap.properties as Record<string, unknown>;
    expect(Object.keys(props).length).toBeLessThanOrEqual(5);
    expect(props).not.toHaveProperty('geometry');
    expect(props).not.toHaveProperty('tags');
    expect(props.name).toBe('第一区');
    expect(props.long_val as string).toHaveLength(60); // 59 chars + ellipsis
    expect((props.long_val as string).endsWith('…')).toBe(true);
  });

  it('resolves parent layer id and keeps bbox/feature identity', () => {
    const snap = buildSelectedFeatureSnapshot(
      { ...base, featureId: 42, bbox: [116.3, 39.8, 116.5, 40.0] },
      ['ref:geojson-abc'],
    );
    expect(snap.layer_id).toBe('ref:geojson-abc'); // parent, NOT the __ sublayer id
    expect(snap.feature_id).toBe(42);
    expect(snap.bbox).toEqual([116.3, 39.8, 116.5, 40.0]);
    expect(snap.point).toEqual([116.4, 39.9]);
  });

  it('feature identity falls back to id-like property, then stable content hash', () => {
    const byProp = buildSelectedFeatureSnapshot(
      { ...base, properties: { OBJECTID: 7, name: 'n' } },
      [],
    );
    expect(byProp.feature_id).toBe(7);

    const a = buildSelectedFeatureSnapshot({ ...base, properties: { name: 'n' } }, []);
    const b = buildSelectedFeatureSnapshot({ ...base, properties: { name: 'n' } }, []);
    expect(typeof a.feature_id).toBe('string');
    expect(a.feature_id).toMatch(/^h-[0-9a-f]{8}$/); // djb2 content hash
    expect(a.feature_id).toBe(b.feature_id); // stable across calls
  });

  it('omits bad bbox silently', () => {
    const snap = buildSelectedFeatureSnapshot(
      { ...base, bbox: [1, 2, 3] as unknown as [number, number, number, number] },
      [],
    );
    expect(snap.bbox).toBeNull();
  });

  it('truncates an oversized feature_id to ≤64 chars, still stable', () => {
    const hugeId = 'x'.repeat(5000);
    const snap = buildSelectedFeatureSnapshot({ ...base, featureId: hugeId }, []);
    expect(snap.feature_id).toBe('x'.repeat(64)); // capped, never multi-KB
    // Stable: the same huge id always maps to the same truncated value.
    expect(buildSelectedFeatureSnapshot({ ...base, featureId: hugeId }, []).feature_id).toBe(
      snap.feature_id,
    );

    // The id-like property fallback is bounded the same way.
    const byProp = buildSelectedFeatureSnapshot(
      { ...base, properties: { OBJECTID: 'y'.repeat(200), name: 'n' } },
      [],
    );
    expect(byProp.feature_id).toBe('y'.repeat(64));

    // Short ids and numbers pass through untouched.
    expect(buildSelectedFeatureSnapshot({ ...base, featureId: 'abc' }, []).feature_id).toBe('abc');
    expect(buildSelectedFeatureSnapshot({ ...base, featureId: 42 }, []).feature_id).toBe(42);
  });
});

describe('useSSEStream mapState snapshot (FE-4 design §7)', () => {
  beforeEach(() => {
    bridgeMock.send.mockClear();
    useHudStore.setState({
      selectedFeature: null,
      focusLayerId: null,
      layers: [],
      baseLayer: 'OSM 地图',
      viewport: { center: [0, 0], zoom: 5, bearing: 0, pitch: 0 },
      is3D: false,
    });
  });

  it('selected_feature is bounded (parent id + ≤5 props) and focus_layer_id flows', async () => {
    useHudStore.setState({
      selectedFeature: {
        layerId: 'ref:geojson-abc__point', // sublayer id must resolve to parent
        layerName: '测试层',
        refId: 'ref:geojson-abc',
        point: [116.4, 39.9],
        properties: {
          name: '第一区',
          area_km2: 12.5,
          geometry: { type: 'Polygon', coordinates: [[[116.3, 39.8]]] },
          tags: { a: 1 },
          extra1: 'v1',
          extra2: 'v2',
          extra3: 'v3',
        },
        selectedAt: 1700000000000,
        featureId: 42,
        bbox: [116.3, 39.8, 116.5, 40.0],
      },
      focusLayerId: 'ref:geojson-abc',
      layers: [{ id: 'ref:geojson-abc' }] as any,
    });

    const mapState = await sendAndGetMapState();
    const sel = mapState.selected_feature as Record<string, unknown>;

    expect(sel.layer_id).toBe('ref:geojson-abc'); // parent, never `__point`
    expect(sel.feature_id).toBe(42);
    expect(sel.bbox).toEqual([116.3, 39.8, 116.5, 40.0]);
    expect((sel.properties as Record<string, unknown>)).not.toHaveProperty('geometry');
    expect((sel.properties as Record<string, unknown>)).not.toHaveProperty('tags');
    expect(Object.keys(sel.properties as Record<string, unknown>).length).toBeLessThanOrEqual(5);
    expect((sel.properties as Record<string, unknown>).name).toBe('第一区');
    expect(mapState.focus_layer_id).toBe('ref:geojson-abc');
    // whole snapshot stays small — no raw feature payload can ride along
    expect(JSON.stringify(sel).length).toBeLessThan(1500);
  });

  it('omits selection/focus entirely when absent', async () => {
    const mapState = await sendAndGetMapState();
    expect(mapState.selected_feature).toBeNull();
    expect(mapState.focus_layer_id).toBeNull();
  });

  it('includes style and legend metadata for cartographic convergence evidence', async () => {
    const legendSpec = {
      type: 'categorical',
      field: 'kind',
      categories: [{ key: 'a', label: 'A', color: '#3366cc' }],
    };
    useHudStore.setState({
      layers: [
        {
          id: 'result',
          name: 'Result',
          type: 'vector',
          visible: true,
          opacity: 0.8,
          style: { color: '#3366cc' },
          legend_spec: legendSpec,
        },
      ] as any,
    });

    const mapState = await sendAndGetMapState();
    const layer = (mapState.layers as Array<Record<string, unknown>>)[0];
    expect(layer.style).toEqual({ color: '#3366cc' });
    expect(layer.legend_spec).toEqual(legendSpec);
    expect(layer).not.toHaveProperty('source');
  });
});

describe('useSSEStream step_cancelled', () => {
  beforeEach(() => {
    bridgeMock.send.mockClear();
    bridgeMock.onEventCallback = null;
    useHudStore.setState({
      selectedFeature: null,
      focusLayerId: null,
      layers: [],
      baseLayer: 'OSM 地图',
      viewport: { center: [0, 0], zoom: 5, bearing: 0, pitch: 0 },
      is3D: false,
    });
  });

  // Render, send once (establishes thinkingMsgIdRef), then attach the seeded
  // tool-call rows to the turn's thinking message (the last message).
  async function renderWithToolCalls(toolCalls: ToolCallEntry[]) {
    const hook = renderStream();
    await act(async () => {
      await hook.result.current.handleSend('hi');
    });
    act(() => {
      hook.result.current.setMessages((prev) => {
        const idx = prev.length - 1;
        return [...prev.slice(0, idx), { ...prev[idx], toolCalls }];
      });
    });
    return hook;
  }

  function emitStepCancelled(data: Record<string, unknown>) {
    act(() => {
      bridgeMock.onEventCallback?.({ event: 'step_cancelled', data });
    });
  }

  it('marks a matching running row failed/已取消, preserving other fields and sibling rows', async () => {
    const hook = await renderWithToolCalls([
      { id: 'step-1', tool: 'search_poi', arguments: '{"q":"school"}', status: 'running', startedAt: 1 },
      { id: 'step-2', tool: 'heatmap_data', status: 'running', startedAt: 2 },
    ]);
    emitStepCancelled({
      task_id: 't1',
      step_id: 'step-1',
      tool: 'search_poi',
      session_id: 'sid-fe4',
    });
    const msg = hook.result.current.messages[hook.result.current.messages.length - 1];
    expect(msg.toolCalls).toEqual([
      { id: 'step-1', tool: 'search_poi', arguments: '{"q":"school"}', status: 'failed', error: '已取消', startedAt: 1 },
      { id: 'step-2', tool: 'heatmap_data', status: 'running', startedAt: 2 },
    ]);
  });

  it('no-ops on unknown step_id, preserving message object identity', async () => {
    const hook = await renderWithToolCalls([
      { id: 'step-1', tool: 'search_poi', status: 'running', startedAt: 1 },
    ]);
    const messagesBefore = hook.result.current.messages;
    const msgBefore = messagesBefore[messagesBefore.length - 1];
    emitStepCancelled({ task_id: 't1', step_id: 'nope', tool: 'search_poi', session_id: 'sid-fe4' });
    expect(hook.result.current.messages).toBe(messagesBefore);
    expect(hook.result.current.messages[hook.result.current.messages.length - 1]).toBe(msgBefore);
  });

  it('never overwrites an already-terminal row while cancelling a running row in the same batch', async () => {
    const hook = await renderWithToolCalls([
      { id: 'step-1', tool: 'search_poi', status: 'completed', result: 'ok', completedAt: 5 },
      { id: 'step-2', tool: 'heatmap_data', status: 'running', startedAt: 2 },
    ]);
    emitStepCancelled({ task_id: 't1', step_id: 'step-1', tool: 'search_poi', session_id: 'sid-fe4' });
    let calls = hook.result.current.messages[hook.result.current.messages.length - 1].toolCalls!;
    expect(calls[0].status).toBe('completed');
    expect(calls[0].result).toBe('ok');
    expect(calls[0].error).toBeUndefined();
    expect(calls[1].status).toBe('running');
    // a later step_cancelled for the running row still lands
    emitStepCancelled({ task_id: 't1', step_id: 'step-2', tool: 'heatmap_data', session_id: 'sid-fe4' });
    calls = hook.result.current.messages[hook.result.current.messages.length - 1].toolCalls!;
    expect(calls[1]).toMatchObject({ id: 'step-2', status: 'failed', error: '已取消' });
  });

  it('leaves a row running when data.tool mismatches on the same step_id', async () => {
    const hook = await renderWithToolCalls([
      { id: 'step-1', tool: 'search_poi', status: 'running', startedAt: 1 },
    ]);
    emitStepCancelled({ task_id: 't1', step_id: 'step-1', tool: 'other_tool', session_id: 'sid-fe4' });
    const calls = hook.result.current.messages[hook.result.current.messages.length - 1].toolCalls!;
    expect(calls[0]).toMatchObject({ id: 'step-1', status: 'running' });
  });
});

describe('canonical MapSpec runtime patch', () => {
  beforeEach(() => {
    useHudStore.setState({
      layers: [{
        id: 'ref:geojson-1',
        name: 'Raw result',
        type: 'vector',
        visible: false,
        opacity: 1,
        source: { type: 'FeatureCollection', features: [] },
        _refId: 'ref:geojson-1',
      }] as any,
      accentColor: '#00aaff',
    });
  });

  it('updates the existing ref layer instead of duplicating it', () => {
    renderStream();
    const legend = {
      type: 'categorical',
      field: 'kind',
      categories: [{ key: 'a', label: 'A', color: '#3366cc' }],
    };

    act(() => {
      bridgeMock.onEventCallback?.({
        event: 'step_result',
        data: {
          tool: 'webgis_layer_upsert',
          geojson_ref: 'ref:geojson-1',
          ref_descriptor: {
            ref_id: 'ref:geojson-1', feature_count: 10000, point_count: 10000,
            geometry_types: ['Point'], bbox: [100, 20, 101, 21],
            mvt_capable: true, estimated_bytes: 1000000,
          },
          result: {
            runtime_patch: {
              layer_id: 'thematic-result',
              result_ref: 'ref:geojson-1',
              visible: true,
              opacity: 0.75,
              style: { color: '#3366cc' },
              legend_spec: legend,
              mapspec_fingerprint: 'carto-sha256:abc',
              repair_attempts: [{ iteration: 1 }],
            },
          },
        },
      });
    });

    const layers = useHudStore.getState().layers;
    expect(layers).toHaveLength(1);
    expect(layers[0]).toEqual(expect.objectContaining({
      id: 'ref:geojson-1',
      visible: true,
      opacity: 0.75,
      legend_spec: legend,
      _mapspecLayerId: 'thematic-result',
      _mapspecFingerprint: 'carto-sha256:abc',
    }));
  });

  it('mounts raster MapSpec output as an image source, never empty GeoJSON', () => {
    useHudStore.setState({ layers: [], accentColor: '#00aaff' });
    renderStream();

    act(() => {
      bridgeMock.onEventCallback?.({
        event: 'step_result',
        data: {
          tool: 'webgis_layer_upsert',
          result: {
            type: 'heatmap_raster',
            image: '/api/v1/sessions/s/raster/r1.png',
            bbox: [116, 39, 117, 40],
            result_ref: 'ref:raster/r1',
            runtime_patch: {
              layer_id: 'ndvi',
              result_ref: 'ref:raster/r1',
              image_ref: 'ref:raster/r1',
              visible: true,
              opacity: 0.85,
              style: { palette: 'viridis' },
              mapspec_fingerprint: 'carto-sha256:raster',
            },
          },
        },
      });
    });

    const layers = useHudStore.getState().layers;
    expect(layers).toHaveLength(1);
    expect(layers[0]).toEqual(expect.objectContaining({
      type: 'heatmap',
      _refId: 'ref:raster/r1',
      _tileUrl: undefined,
      _mapspecLayerId: 'ndvi',
      source: expect.objectContaining({
        image: '/api/v1/sessions/s/raster/r1.png',
        bbox: [116, 39, 117, 40],
      }),
    }));
    expect(layers[0].source).not.toEqual(expect.objectContaining({
      type: 'FeatureCollection',
    }));
  });

  it('INV-2: stale event from old session A does not flip active session B or mutate state', () => {
    const setSid = vi.fn();
    const sidRef = { current: 'session-B' };
    renderHook(() =>
      useSSEStream(
        'session-B',
        setSid,
        sidRef,
        dispatchAction,
        getMapSnapshot,
        null,
        { current: null },
      )
    );

    act(() => {
      // Event arriving with session_id: 'session-A'
      bridgeMock.onEventCallback?.({
        event: 'step_result',
        data: {
          session_id: 'session-A',
          tool: 'search_poi',
          name: 'Stale POI',
        },
      });
    });

    // setSessionId must NOT have been called with 'session-A'!
    expect(setSid).not.toHaveBeenCalledWith('session-A');
    expect(sidRef.current).toBe('session-B');
  });

  it('INV-4: task_cancelled event marks thinking message as cancelled without fabricating fake completion', async () => {
    const { result } = renderHook(() =>
      useSSEStream(
        'session-1',
        vi.fn(),
        { current: 'session-1' },
        dispatchAction,
        getMapSnapshot,
        null,
        { current: null },
      )
    );

    let sendPromise: Promise<void> | undefined;
    act(() => {
      sendPromise = result.current.handleSend('cancel me');
    });

    // Simulate task_cancelled event from server
    act(() => {
      bridgeMock.onEventCallback?.({
        event: 'task_cancelled',
        data: { session_id: 'session-1', task_id: 't-cancel' },
      });
    });

    await act(async () => {
      await sendPromise;
    });

    const msgs = result.current.messages;
    const lastMsg = msgs[msgs.length - 1];
    expect(lastMsg.content).toContain('已取消');
    expect(lastMsg.content).not.toBe('完成。');
    expect(lastMsg.isThinking).toBe(false);
  });
});



describe('useSSEStream — turn-scoped tool-arg evidence (#466)', () => {
  beforeEach(() => {
    bridgeMock.send.mockClear();
    bridgeMock.aiStatus = 'idle';
    useHudStore.getState().clearResults();
  });

  function fire(event: string, data: Record<string, unknown>) {
    act(() => {
      bridgeMock.onEventCallback?.({ event, data });
    });
  }

  it('a turn start clears the previous interrupted turn\'s pending tool args', async () => {
    const { result } = renderStream();

    // Turn A: tool_call captured, stream cut before step_result/task end.
    fire('tool_call', { name: 'buffer_analysis', arguments: '{"distance":300}' });

    // Turn B starts (the production handleSend path).
    await act(async () => {
      await result.current.handleSend('重试缓冲区分析');
    });

    // Turn B: same tool runs to completion.
    fire('tool_call', { name: 'buffer_analysis', arguments: '{"distance":900}' });
    fire('step_result', { step_id: 's-b2', tool: 'buffer_analysis', session_id: 'sid-fe4', result: { success: true, summary: 'ok' } });

    const r = useHudStore.getState().results.find((x) => x.id === 's-b2');
    expect(r).toBeDefined();
    // WITHOUT the turn-start reset the FIFO hands Turn B's result Turn A's args.
    expect(r!.parameters).toEqual(
      expect.arrayContaining([expect.objectContaining({ source: 'distance', value: 900 })]),
    );
    expect(r!.parameters).toEqual(
      expect.not.arrayContaining([expect.objectContaining({ value: 300 })]),
    );
  });

  it('task_cancelled drops remaining pending args (a retry pairs with its own)', async () => {
    const { result } = renderStream();
    await act(async () => {
      await result.current.handleSend('分析');
    });

    fire('tool_call', { name: 'hotspot_analysis', arguments: '{"distance":100}' });
    fire('tool_call', { name: 'hotspot_analysis', arguments: '{"distance":200}' }); // queued, never ran
    fire('task_cancelled', { session_id: 'sid-fe4', task_id: 't1' });

    // Retry turn: fresh args only.
    await act(async () => {
      await result.current.handleSend('重试');
    });
    fire('tool_call', { name: 'hotspot_analysis', arguments: '{"distance":950}' });
    fire('step_result', { step_id: 's-r', tool: 'hotspot_analysis', session_id: 'sid-fe4', result: { success: true } });

    const r = useHudStore.getState().results.find((x) => x.id === 's-r');
    expect(r).toBeDefined();
    expect(r!.parameters).toEqual(
      expect.arrayContaining([expect.objectContaining({ source: 'distance', value: 950 })]),
    );
    expect(r!.parameters).toEqual(
      expect.not.arrayContaining([expect.objectContaining({ value: 100 })]),
    );
  });
});

describe('useSSEStream — plan approval rollback (#468)', () => {
  function fire(event: string, data: Record<string, unknown>) {
    act(() => {
      bridgeMock.onEventCallback?.({ event, data });
    });
  }

  beforeEach(() => {
    bridgeMock.send.mockReset();
    bridgeMock.send.mockResolvedValue(undefined);
    bridgeMock.aiStatus = 'idle';
    useHudStore.setState({ aiStatus: 'idle' });
  });

  async function renderWithApprovedPlan() {
    const { result } = renderStream();
    await act(async () => {
      await result.current.handleSend('给我一个分析计划');
    });
    // propose_plan step_result attaches the plan card to the thinking message.
    fire('step_result', {
      step_id: 's-plan',
      tool: 'propose_plan',
      session_id: 'sid-fe4',
      result: {
        success: true,
        plan_id: 'plan-1',
        title: '学校密度分析',
        summary: '三步',
        step_count: 3,
        destructive_steps: [],
        steps_preview: [],
      },
    });
    const withPlan = result.current.messages.find((m) => (m as any).plan);
    expect((withPlan as any)?.plan.status).toBe('pending');
    return { result };
  }

  it('reverts the optimistic approval when the follow-up send fails', async () => {
    const { result } = await renderWithApprovedPlan();

    // The deferred send fails like a dead stream: the bridge resolves with the
    // store's aiStatus left at 'error' (useMapBridge's terminal handling).
    bridgeMock.send.mockImplementation(async () => {
      useHudStore.setState({ aiStatus: 'error' });
    });

    act(() => {
      result.current.handlePlanAction('plan-1', 'approve');
    });
    // Optimistic lock applied immediately…
    const planMsg = () =>
      (result.current.messages.find((m) => (m as any).plan) as any)?.plan;
    expect(planMsg().status).toBe('approved');

    // …then the deferred send runs and fails → the card must roll back to
    // pending (actionable again), not stay locked forever.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(planMsg().status).toBe('pending');
  });

  it('keeps the approval when the follow-up send succeeds', async () => {
    const { result } = await renderWithApprovedPlan();
    bridgeMock.send.mockImplementation(async () => {
      useHudStore.setState({ aiStatus: 'done' });
    });

    act(() => {
      result.current.handlePlanAction('plan-1', 'approve');
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    const planMsg = (result.current.messages.find((m) => (m as any).plan) as any)?.plan;
    expect(planMsg.status).toBe('approved');
  });

  it('reject path also rolls back (send throws)', async () => {
    const { result } = await renderWithApprovedPlan();
    bridgeMock.send.mockRejectedValue(new Error('network down'));

    act(() => {
      result.current.handlePlanAction('plan-1', 'reject');
    });
    const planMsg = () =>
      (result.current.messages.find((m) => (m as any).plan) as any)?.plan;
    expect(planMsg().status).toBe('rejected');

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(planMsg().status).toBe('pending');
  });
});
