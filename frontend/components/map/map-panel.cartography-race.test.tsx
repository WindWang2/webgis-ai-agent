/* eslint-disable @typescript-eslint/no-require-imports --
 * vi.mock factories are hoisted above top-level imports by vitest; referencing
 * module-scope variables would TDZ, so factories require() instead (vitest
 * official pattern). Scoped to this test file. */
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MapPanel } from './map-panel';
import type { Layer } from '@/lib/types/layer';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';

/**
 * Cartographic observation→repair generation-safety (latest-generation-wins).
 *
 * The repair loop fires one client_generation-stamped POST per meaningful
 * MapSpec reconcile. These tests drive the real component through real
 * reconciles with a CONTROLLABLE fetch (deferred per call) so response arrival
 * order is fully deterministic. They pin the invariants:
 *   INV-1 latest generation wins (out-of-order same-session response)
 *   INV-2 session switch drops the in-flight response
 *   INV-3 duplicate repair applied at most once
 *   INV-4 stale mapspec_fingerprint repair ignored
 *   INV-5 a failed observation may be retried by the next meaningful reconcile
 *   INV-6 unmount with an outstanding request produces no side effect
 *   INV-7 a valid latest-generation repair is still applied
 */

// ─── hoisted registries (bridge mock factories ↔ assertions) ────────────────

const rmg = vi.hoisted(() => ({
  renderCount: 0,
  lastOnLoad: null as null | (() => void),
  map: null as any,
}));

const hud = vi.hoisted(() => {
  const initialState = () => ({
    is3D: false,
    processLayers: {} as Record<string, unknown>,
    cartographyTitle: null as string | null,
    viewport: { center: [116.4, 39.9], zoom: 4, bearing: 0, pitch: 0, bounds: undefined },
    focusLayerId: null as string | null,
    aiStatus: 'idle',
    selectedFeature: null as any,
    mapLoaded: false,
    baseLayer: 'Carto 深色',
  });
  const actions = {
    setMapLoaded: (v: boolean) => hud.setState({ mapLoaded: v }),
    setSelectedFeature: (f: any) => hud.setState({ selectedFeature: f }),
    setViewport: (c: any, z: number, b: number, p: number, bounds?: any) =>
      hud.setState({ viewport: { center: c, zoom: z, bearing: b, pitch: p, bounds } }),
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
  };
  const state: Record<string, unknown> = { ...initialState(), ...actions };
  const listeners = new Set<() => void>();
  return {
    state,
    listeners,
    getState: () => state,
    setState: (partial: Record<string, unknown>) => {
      Object.assign(state, partial);
      listeners.forEach((l) => l());
    },
    reset: () => {
      Object.keys(state).forEach((k) => delete state[k]);
      Object.assign(state, initialState(), actions);
    },
  };
});

// Stable dispatch + logger spies the assertions read.
const dispatch = vi.hoisted(() => vi.fn());
const logger = vi.hoisted(() => ({
  warn: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
}));

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.renderCount += 1;
    rmg.lastOnLoad = props.onLoad ?? null;
    React.useEffect(() => {
      // Fire onLoad inside the mount effect (RTL wraps it in act) so mapReady
      // flips synchronously and the runtime is created before we proceed.
      props.onLoad?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement('div', { 'data-testid': 'map-mock' }, props.children);
  });
  const PopupMock = (props: any) =>
    React.createElement('div', { 'data-testid': 'popup' }, props.children);
  return { default: MapMock, Popup: PopupMock };
});

vi.mock('@/lib/store/useHudStore', () => {
  const React = require('react');
  const { useSyncExternalStore } = React;
  const subscribe = (cb: () => void) => {
    hud.listeners.add(cb);
    return () => hud.listeners.delete(cb);
  };
  const useHudStore = (selector: (s: any) => unknown) =>
    useSyncExternalStore(subscribe, () => selector(hud.state), () => selector(hud.state));
  useHudStore.getState = () => hud.state;
  useHudStore.setState = (partial: any) => hud.setState(partial);
  useHudStore.subscribe = subscribe;
  return { useHudStore };
});

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    actions: [],
    dispatchAction: dispatch,
    popAction: vi.fn(),
    selectedBaseLayer: 1,
    setSelectedBaseLayer: vi.fn(),
    registerSnapshotFn: vi.fn(),
    getMapSnapshot: vi.fn(() => null),
    registerAckSink: vi.fn(() => () => {}),
    reportTerminal: vi.fn(),
    clearActions: vi.fn(),
    droppedCount: 0,
    completedActions: [],
    runningActionId: null,
  }),
}));

vi.mock('./map-action-handler', () => ({ MapActionHandler: () => null }));
vi.mock('./thematic-legend', () => {
  const React = require('react');
  return { ThematicLegend: () => React.createElement('div', { 'data-testid': 'legend-mock' }) };
});
vi.mock('./map-decorations', () => {
  const React = require('react');
  return { MapDecorations: () => React.createElement('div', { 'data-testid': 'decor-mock' }) };
});
vi.mock('@/lib/utils/logger', () => ({
  devOnly: logger,
  logger: logger,
}));

// ─── controllable fetch ────────────────────────────────────────────────────

interface PendingCall {
  resolve: (r: Response) => void;
  reject: (e: unknown) => void;
  settled: boolean;
}

function makeAbortError(): Error {
  const err = new Error('The user aborted a request.');
  err.name = 'AbortError';
  return err;
}

function makeControllableFetch(opts: { respectSignal?: boolean } = {}) {
  const respectSignal = opts.respectSignal ?? false;
  const calls: Array<{ url: string; init: RequestInit; body: any }> = [];
  const pending: PendingCall[] = [];
  const fetchMock = vi.fn((url: string, init: RequestInit) => {
    let body: any = undefined;
    try {
      body = init.body ? JSON.parse(String(init.body)) : undefined;
    } catch {
      body = undefined;
    }
    calls.push({ url, init, body });
    return new Promise<Response>((resolve, reject) => {
      const entry: PendingCall = { resolve, reject, settled: false };
      pending.push(entry);
      // When respectSignal is set, the mock behaves like real fetch: an aborted
      // signal rejects with AbortError, exercising the production
      // timedFetch → AbortError → catch path.
      if (respectSignal) {
        const signal = init.signal as AbortSignal | undefined;
        if (signal) {
          if (signal.aborted) {
            entry.settled = true;
            reject(makeAbortError());
          } else {
            signal.addEventListener('abort', () => {
              if (!entry.settled) {
                entry.settled = true;
                reject(makeAbortError());
              }
            });
          }
        }
      }
    });
  });
  return {
    fetchMock,
    calls,
    callBody: (i: number) => calls[i]?.body,
    resolveJson(i: number, json: unknown) {
      const entry = pending[i];
      if (entry && !entry.settled) {
        entry.settled = true;
        entry.resolve(
          new Response(JSON.stringify(json), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
      }
    },
    rejectError(i: number, err: unknown) {
      const entry = pending[i];
      if (entry && !entry.settled) {
        entry.settled = true;
        entry.reject(err);
      }
    },
  };
}

/** A repair_action the backend would mint for one MapSpec generation. */
function repairAction(
  actionId: string,
  mapspecFingerprint: string,
  patchFingerprint = `repair-sha256:${actionId}`,
) {
  return {
    observation_accepted: true,
    repair_action: {
      action_id: actionId,
      command: 'cartographic_runtime_repair',
      correlation: { session_id: 's', step_id: '' },
      params: {
        mapspec_fingerprint: mapspecFingerprint,
        observation_sequence: 1,
        patch_fingerprint: patchFingerprint,
        repair_patches: [],
      },
    },
  };
}

// ─── helpers ────────────────────────────────────────────────────────────────

const noop = () => {};

function freshMockMap() {
  const map = makeMockMaplibreMap();
  map.isStyleLoaded = vi.fn(() => true);
  map.setTerrain = vi.fn();
  map.getBounds = vi.fn(() => ({
    getWest: () => 116.3, getSouth: () => 39.8, getEast: () => 116.5, getNorth: () => 40.0,
  }));
  return map;
}

/** A layer that triggers exactly one cartographic observation per fingerprint. */
function fingerprintLayer(id: string, fingerprint: string): Layer {
  return {
    id,
    name: id,
    type: 'vector',
    visible: true,
    opacity: 1,
    source: {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', properties: { v: 1 }, geometry: { type: 'Point', coordinates: [116.4, 39.9] } },
      ],
    },
    style: { color: '#16a34a' },
    _refId: id,
    _mapspecLayerId: id,
    _mapspecFingerprint: fingerprint,
    _mapspecGenerationAt: 1,
  } as Layer;
}

const FP_A = 'carto-sha256:aaaa1111bbbb2222cccc3333dddd4444';
const FP_B = 'carto-sha256:zzzz9999yyyy8888xxxx7777wwww6666';
const FP_C = 'carto-sha256:qqqq5555rrrr4444ssss3333tttt2222';

function panelProps(layers: Layer[], sessionId = 'session-A', ownerToken = 'owner-token') {
  return {
    layers,
    onRemoveLayer: noop,
    onToggleLayer: noop,
    onViewportChange: noop,
    sessionId,
    ownerToken,
  };
}

async function mountPanel(layers: Layer[], sessionId = 'session-A') {
  const view = render(<MapPanel {...panelProps(layers, sessionId)} />);
  // onLoad fires inside the mount effect → mapReady → runtime created + first
  // reconcile enqueued; drain the debounced apply chain so the observation POST
  // has been issued before we touch the controllable fetch.
  await waitFor(() => expect(rmg.renderCount).toBeGreaterThan(1));
  await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
  return view;
}

async function rerenderPanel(view: ReturnType<typeof render>, layers: Layer[], sessionId = 'session-A') {
  view.rerender(<MapPanel {...panelProps(layers, sessionId)} />);
  await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
}

/** Flush microtasks so a resolved fetch's .then dispatch runs inside act. */
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}

const dispatchedIds = () => dispatch.mock.calls.map((c) => (c[0] as any)?.action_id);

// ─── tests ──────────────────────────────────────────────────────────────────

describe('MapPanel — cartographic repair generation safety', () => {
  let fetchCtl: ReturnType<typeof makeControllableFetch>;

  beforeEach(() => {
    hud.reset();
    rmg.map = freshMockMap();
    rmg.renderCount = 0;
    rmg.lastOnLoad = null;
    dispatch.mockClear();
    logger.warn.mockClear();
    logger.error.mockClear();
    fetchCtl = makeControllableFetch();
    vi.stubGlobal('fetch', fetchCtl.fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // TEST-1 — out-of-order same-session response: only the newest applies.
  it('applies only the newer repair when B responds before A (INV-1)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    // A second meaningful reconcile (new fingerprint) issues B while A is pending.
    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));

    // Network returns B first, then the stale A.
    fetchCtl.resolveJson(1, repairAction('r-B', FP_B));
    await flush();
    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();

    expect(dispatchedIds()).toEqual(['r-B']);
  });

  // TEST-2 — a response from session A must not touch session B.
  it('drops the in-flight response after a session switch (INV-2)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)], 'session-A');
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    // Switch session (and drop the fingerprint layer so no new observation fires).
    await rerenderPanel(view, [], 'session-B');
    expect(fetchCtl.calls).toHaveLength(1);

    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();

    expect(dispatchedIds()).toEqual([]);
  });

  // TEST-3 — duplicate repair_action (same action_id) applied at most once.
  it('applies a duplicate repair at most once even across generations (INV-3)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();
    expect(dispatchedIds()).toEqual(['r-A']);

    // A newer generation legitimately returns the SAME repair (e.g. a retried /
    // cached repair re-echoed). Generation gate would let it through; dedup must
    // not re-apply it.
    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));
    fetchCtl.resolveJson(1, repairAction('r-A', FP_B, 'repair-sha256:r-A'));
    await flush();

    expect(dispatchedIds()).toEqual(['r-A']);
  });

  // TEST-4 — a failed observation is retried by the next meaningful reconcile.
  it('recovers from a failed observation without a retry storm (INV-5)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    // Network failure → catch resets the observation key so a later reconcile
    // may retry. No new request is spawned by the failure itself.
    fetchCtl.rejectError(0, new TypeError('network down'));
    await flush();
    expect(fetchCtl.calls).toHaveLength(1);
    expect(logger.warn).toHaveBeenCalledTimes(1);

    // Next meaningful reconcile (new fingerprint) re-issues and succeeds.
    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));
    fetchCtl.resolveJson(1, repairAction('r-B', FP_B));
    await flush();

    expect(dispatchedIds()).toEqual(['r-B']);
    expect(fetchCtl.calls).toHaveLength(2); // exactly one retry — no storm
  });

  // TEST-5 — a repair targeting a stale fingerprint is ignored (fp gate, INV-4).
  it('ignores a repair whose mapspec_fingerprint is no longer current (INV-4)', async () => {
    await mountPanel([fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    // Backend (bug/cache) echoes a repair for an OLD fingerprint although the
    // latest issued observation targets FP_B. The fp gate must drop it.
    fetchCtl.resolveJson(0, repairAction('r-stale', FP_A));
    await flush();

    expect(dispatchedIds()).toEqual([]);
  });

  // TEST-6 — a valid latest-generation repair is still applied (INV-7).
  it('applies a valid latest-generation repair (INV-7)', async () => {
    await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();

    expect(dispatchedIds()).toEqual(['r-A']);
    expect(logger.warn).not.toHaveBeenCalled();
  });

  // TEST-7 — unmount with an outstanding request produces no side effect.
  it('dispatches nothing when unmounted with an outstanding request (INV-6)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));

    view.unmount();
    // The late response resolves after unmount.
    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();

    expect(dispatchedIds()).toEqual([]);
  });

  // Bonus — issuing a newer observation aborts the prior in-flight request's
  // signal (the AbortController wiring the generation gate backs up).
  it('aborts the previous in-flight request signal when a newer observation issues', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    const priorSignal = fetchCtl.calls[0].init.signal as AbortSignal;

    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));

    // AbortController.abort() flips signal.aborted synchronously.
    expect(priorSignal.aborted).toBe(true);
    // The newer request is unaffected.
    expect((fetchCtl.calls[1].init.signal as AbortSignal).aborted).toBe(false);
  });

  // End-to-end abort path: with a signal-respecting fetch, a supersede rejects
  // the prior request with AbortError; the catch must swallow it SILENTLY (no
  // warn, no observation-key reset) so no redundant retry re-POSTs (INV-5).
  it('silently swallows a supersede AbortError (no warn, no retry re-POST)', async () => {
    fetchCtl = makeControllableFetch({ respectSignal: true });
    vi.stubGlobal('fetch', fetchCtl.fetchMock);

    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    // Issuing B aborts A → A's fetch rejects with AbortError → catch swallows it.
    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));
    await flush();

    expect(logger.warn).not.toHaveBeenCalled();
    // B still resolves and dispatches normally.
    fetchCtl.resolveJson(1, repairAction('r-B', FP_B));
    await flush();

    expect(dispatchedIds()).toEqual(['r-B']);
    expect(fetchCtl.calls).toHaveLength(2); // no extra retry POST of B
  });

  // Timeout (ApiTimeoutError) must NOT be swallowed as an abort — the key resets
  // so the next meaningful reconcile retries (INV-5). This pins the error-name
  // classification in the catch (the most likely regression target).
  it('retries after a timeout error without classifying it as an abort (INV-5)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    const timeout = new Error('timed out after 30000ms');
    timeout.name = 'ApiTimeoutError';
    fetchCtl.rejectError(0, timeout);
    await flush();
    // Not an AbortError ⇒ surfaced (warn) and the key reset for retry.
    expect(logger.warn).toHaveBeenCalledTimes(1);

    // Next meaningful reconcile re-issues and succeeds.
    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));
    fetchCtl.resolveJson(1, repairAction('r-B', FP_B));
    await flush();

    expect(dispatchedIds()).toEqual(['r-B']);
  });

  // Two distinct valid repairs in sequence must BOTH apply — guards against an
  // over-aggressive dedup that would drop every other repair.
  it('applies two distinct repairs across two generations', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();

    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));
    fetchCtl.resolveJson(1, repairAction('r-B', FP_B));
    await flush();

    expect(dispatchedIds()).toEqual(['r-A', 'r-B']);
  });

  // A,B,A′: a duplicate action_id re-echoed at a NEWER (non-adjacent) generation
  // is applied at most once. Distinguishes the bounded-Set dedup from a single
  // last-key (which would re-apply A′ after B). (INV-3)
  it('does not re-apply an action_id seen in a non-adjacent generation (INV-3)', async () => {
    const view = await mountPanel([fingerprintLayer('L', FP_A)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(1));
    fetchCtl.resolveJson(0, repairAction('r-A', FP_A));
    await flush();

    await rerenderPanel(view, [fingerprintLayer('L', FP_B)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(2));
    fetchCtl.resolveJson(1, repairAction('r-B', FP_B));
    await flush();

    // Third generation re-echoes the FIRST action_id at the latest generation.
    await rerenderPanel(view, [fingerprintLayer('L', FP_C)]);
    await waitFor(() => expect(fetchCtl.calls).toHaveLength(3));
    fetchCtl.resolveJson(2, repairAction('r-A', FP_C));
    await flush();

    expect(dispatchedIds()).toEqual(['r-A', 'r-B']);
  });
});
