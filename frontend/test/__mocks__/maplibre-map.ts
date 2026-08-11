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
  const onceListeners = new Map<string, Set<(...args: any[]) => void>>();
  const onListeners = new Map<string, Set<(...args: any[]) => void>>();

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
    setFilter: vi.fn((layerId: string, filter: any) => {
      calls.setFilter.push({ layerId, filter });
    }),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),

    // ─── Camera ────────────────────────────────────────────────────────────
    flyTo: vi.fn((opts: any) => {
      calls.flyTo.push(opts);
    }),
    fitBounds: vi.fn((bbox: [number, number, number, number], opts?: any) => {
      calls.fitBounds.push({ bbox, options: opts });
    }),
    jumpTo: vi.fn(),
    easeTo: vi.fn(),
    stop: vi.fn(() => {
      calls.stop.push(1);
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
