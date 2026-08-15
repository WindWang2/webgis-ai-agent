import { vi } from 'vitest';

/**
 * Shared MapLibre mock factory (Harness–Map Interaction V3, design §6/§9).
 *
 * House style (runtime.test.ts): hand-roll a stub map that records the calls
 * the code under test makes, then assert on the call sequence. This module is
 * the shared version of the three hand-rolled stubs that used to live in
 * map-action-handler.test.tsx and the mapspec-runtime tests.
 *
 * The returned map:
 * - keeps mutable viewport state (getCenter/getZoom/getBearing/getPitch) so
 *   camera commands can report the *settled* viewport as `actual`;
 * - keeps source/layer bookkeeping (getSource/getLayer/getStyle, add/remove)
 *   so commands and helpers that read the style index behave like a real map;
 * - has a real event emitter (on/once/off) — unlike the old synchronous stubs,
 *   events fire only when the test calls `map._fire(event)`, giving tests full
 *   control over `moveend`/`render` timing (fake-timer friendly);
 * - logs every call on the map itself as `_calls` and keeps every method a
 *   vi.fn, so both `expect(map._calls.addSource)` and
 *   `expect(map.addSource).toHaveBeenCalledWith(...)` styles work.
 *
 * Underscore-prefixed helpers are test-only and not part of the MapLibre API:
 * - `_fire(event, payload?)` — dispatch to registered once/on listeners;
 * - `_setViewport(partial)` — mutate the camera state;
 * - `_calls` / `_sources` / `_layers` / `_viewport` — introspection.
 */

export interface MockMapViewport {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
}

export interface MockMapCallLog {
  flyTo: Array<Record<string, unknown>>;
  fitBounds: Array<{ bbox: [number, number, number, number]; options?: Record<string, unknown> }>;
  stop: number[];
  triggerRepaint: number[];
  addSource: Array<{ id: string; def: unknown }>;
  removeSource: string[];
  addLayer: Array<{ def: Record<string, unknown>; beforeId?: string | null }>;
  removeLayer: string[];
  moveLayer: Array<{ id: string; beforeId?: string | null }>;
  setFilter: Array<{ layerId: string; filter: unknown }>;
  setData: Array<{ id: string; data: unknown }>;
}

export interface MakeMockMaplibreMapOptions {
  center?: [number, number];
  zoom?: number;
  bearing?: number;
  pitch?: number;
}

export function makeMockMaplibreMap(options: MakeMockMaplibreMapOptions = {}) {
  const viewport: MockMapViewport = {
    center: options.center ?? [116.4, 39.9],
    zoom: options.zoom ?? 10,
    bearing: options.bearing ?? 0,
    pitch: options.pitch ?? 0,
  };

  const sources: Record<string, any> = {};
  const layers: Array<Record<string, any>> = [];
  const filters: Record<string, unknown> = {};
  const onceListeners = new Map<string, Set<(...args: any[]) => void>>();
  const onListeners = new Map<string, Set<(...args: any[]) => void>>();
  // ROUND-2: track whether a camera animation is in flight so stop() can model
  // MapLibre reality — stopping an in-flight animation fires moveend
  // SYNCHRONOUSLY (HandlerManager._stop → _afterEase → moveend). flyTo/fitBounds/
  // easeTo start an animation; jumpTo/moveend end it.
  let animating = false;

  const calls: MockMapCallLog = {
    flyTo: [],
    fitBounds: [],
    stop: [],
    triggerRepaint: [],
    addSource: [],
    removeSource: [],
    addLayer: [],
    removeLayer: [],
    moveLayer: [],
    setFilter: [],
    setData: [],
  };

  const emit = (event: string, payload?: unknown) => {
    if (event === 'moveend') animating = false;
    const once = onceListeners.get(event);
    if (once) {
      // once handlers are deleted as they fire (MapLibre semantics); Array.from
      // copies so handlers that re-register during emission are not visited twice.
      Array.from(once).forEach((handler) => {
        once.delete(handler);
        handler(payload);
      });
    }
    const on = onListeners.get(event);
    if (on) {
      Array.from(on).forEach((handler) => handler(payload));
    }
  };

  const map: any = {
    // ─── Viewport (mutable) ────────────────────────────────────────────────
    getCenter: vi.fn(() => ({ lng: viewport.center[0], lat: viewport.center[1] })),
    getZoom: vi.fn(() => viewport.zoom),
    getBearing: vi.fn(() => viewport.bearing),
    getPitch: vi.fn(() => viewport.pitch),

    // ─── Style bookkeeping ─────────────────────────────────────────────────
    getStyle: vi.fn(() => ({ sources, layers })),
    getSource: vi.fn((id: string) => sources[id] ?? null),
    getLayer: vi.fn((id: string) => layers.find((l) => l.id === id) ?? null),
    addSource: vi.fn((id: string, def: any) => {
      sources[id] = {
        ...def,
        setData: vi.fn((data: any) => {
          calls.setData.push({ id, data });
        }),
      };
      calls.addSource.push({ id, def });
    }),
    removeSource: vi.fn((id: string) => {
      delete sources[id];
      calls.removeSource.push(id);
    }),
    addLayer: vi.fn((def: any, beforeId?: string | null) => {
      layers.push(def);
      calls.addLayer.push({ def, beforeId: beforeId ?? null });
    }),
    removeLayer: vi.fn((id: string) => {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    }),
    moveLayer: vi.fn((id: string, beforeId?: string | null) => {
      calls.moveLayer.push({ id, beforeId: beforeId ?? null });
    }),
    // Issue #393: setFilter/getFilter bookkeeping — the mock stores the applied
    // filter per layer so commands can run their post-mutation verification
    // (a real MapLibre map records the expression; getFilter returns it).
    setFilter: vi.fn((layerId: string, filter: any) => {
      filters[layerId] = filter ?? null;
      calls.setFilter.push({ layerId, filter });
    }),
    getFilter: vi.fn((layerId: string) => filters[layerId] ?? null),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),

    // ─── Camera ────────────────────────────────────────────────────────────
    flyTo: vi.fn((opts: any) => {
      calls.flyTo.push(opts);
      animating = true;
    }),
    fitBounds: vi.fn((bbox: [number, number, number, number], opts?: any) => {
      calls.fitBounds.push({ bbox, options: opts });
      animating = true;
    }),
    jumpTo: vi.fn((opts: any = {}) => {
      // Instant move — applies the target immediately and fires moveend
      // synchronously (real MapLibre jumpTo ends the camera state + moveend).
      if (Array.isArray(opts.center)) viewport.center = opts.center;
      if (opts.zoom !== undefined) viewport.zoom = opts.zoom;
      if (opts.bearing !== undefined) viewport.bearing = opts.bearing;
      if (opts.pitch !== undefined) viewport.pitch = opts.pitch;
      emit('moveend');
    }),
    easeTo: vi.fn(() => {
      animating = true;
    }),
    stop: vi.fn(() => {
      calls.stop.push(1);
      // Model MapLibre reality: stop() during an in-flight animation fires
      // moveend SYNCHRONOUSLY — the interrupt moveend a user grab produces
      // mid-flyTo (HandlerManager._stop(true) → _afterEase → moveend).
      if (animating) {
        animating = false;
        emit('moveend');
      }
    }),

    // ─── Events ────────────────────────────────────────────────────────────
    on: vi.fn((event: string, handler: (...args: any[]) => void) => {
      if (!onListeners.has(event)) onListeners.set(event, new Set());
      onListeners.get(event)!.add(handler);
      return map;
    }),
    once: vi.fn((event: string, handler: (...args: any[]) => void) => {
      if (!onceListeners.has(event)) onceListeners.set(event, new Set());
      onceListeners.get(event)!.add(handler);
      return map;
    }),
    off: vi.fn((event: string, handler?: (...args: any[]) => void) => {
      if (handler) {
        onceListeners.get(event)?.delete(handler);
        onListeners.get(event)?.delete(handler);
      } else {
        onceListeners.delete(event);
        onListeners.delete(event);
      }
      return map;
    }),

    triggerRepaint: vi.fn(() => {
      calls.triggerRepaint.push(1);
    }),

    // ─── Test helpers (underscore-prefixed, not part of MapLibre's API) ───
    _calls: calls,
    _sources: sources,
    _layers: layers,
    _viewport: viewport,
    _setViewport(partial: Partial<MockMapViewport>) {
      if (partial.center) viewport.center = partial.center;
      if (partial.zoom !== undefined) viewport.zoom = partial.zoom;
      if (partial.bearing !== undefined) viewport.bearing = partial.bearing;
      if (partial.pitch !== undefined) viewport.pitch = partial.pitch;
    },
    _fire: emit,
  };

  return map;
}

export type MockMaplibreMap = ReturnType<typeof makeMockMaplibreMap>;
