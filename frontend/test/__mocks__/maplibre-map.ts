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
 * Completeness contract (issue #404): every MapLibre method the application
 * code touches (frontend/lib + frontend/components) must exist on this mock —
 * enforced by test/maplibre-mock-surface.test.ts, which statically scans the
 * sources for `map.<method>` accesses. Before that contract, a missing method
 * made any test walking such a path TypeError instead of asserting, so those
 * paths (style-loaded gating, rendered-feature queries, bounds/viewport
 * reading, canvas export, image bookkeeping, DPI management, controls and
 * terrain) were silently untested.
 *
 * Style mutations are stateful, not just logged:
 * - moveLayer actually reorders `_layers` (no beforeId → end of the array,
 *   which is the top of the z-order);
 * - setLayoutProperty/setPaintProperty write into the target layer def's
 *   layout/paint, and getLayoutProperty/getPaintProperty read them back — so
 *   render assertions can be written against the mock's style state.
 *
 * Underscore-prefixed helpers are test-only and not part of the MapLibre API:
 * - `_fire(event, payload?)` — dispatch to registered once/on listeners;
 * - `_setViewport(partial)` — mutate the camera state;
 * - `_calls` / `_sources` / `_layers` / `_images` / `_terrain` / `_controls` /
 *   `_viewport` — introspection.
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
  addControl: Array<{ control: unknown; position?: string | null }>;
  setTerrain: Array<{ options: unknown }>;
  queryRenderedFeatures: Array<{ geometry: unknown; params?: unknown }>;
  setLayoutProperty: Array<{ layerId: string; name: string; value: unknown }>;
  setPaintProperty: Array<{ layerId: string; name: string; value: unknown }>;
}

export interface MakeMockMaplibreMapOptions {
  center?: [number, number];
  zoom?: number;
  bearing?: number;
  pitch?: number;
  /** `isStyleLoaded()` result — default true: tests assume the base style is
   *  loaded synchronously unless they explicitly exercise the deferral path. */
  styleLoaded?: boolean;
  /** `queryRenderedFeatures()` results. Pass an array (fixed) or a zero-arg
   *  function (lazy — re-read on every call, e.g. a mutable test registry). */
  renderedFeatures?: unknown[] | (() => unknown[]);
  /** `getBounds()` corners as [west, south, east, north]. Default derives from
   *  center ± 0.1°, so the default viewport yields [116.3, 39.8, 116.5, 40.0]. */
  bounds?: [number, number, number, number];
}

/** Minimal 2D-context stub (mirrors test/setup.ts) so drawing-heavy paths
 *  (exporter composition) can run without a real GL/2D context. */
const canvas2dContext: Record<string, unknown> = {
  drawImage: () => {},
  fillText: () => {},
  fillRect: () => {},
  createLinearGradient: () => ({ addColorStop: () => {} }),
  beginPath: () => {},
  moveTo: () => {},
  lineTo: () => {},
  closePath: () => {},
  fill: () => {},
  stroke: () => {},
  strokeRect: () => {},
  arc: () => {},
  arcTo: () => {},
  save: () => {},
  restore: () => {},
  translate: () => {},
  rotate: () => {},
  setLineDash: () => {},
  measureText: () => ({ width: 50 }),
  fillStyle: '',
  strokeStyle: '',
  lineWidth: 1,
  font: '',
  textAlign: 'left',
};

/** Canvas-like object returned by `getCanvas()`: real jsdom canvases cannot
 *  `toBlob`, so the mock hands back a self-contained fake with the surface the
 *  exporter actually uses (width/height, toBlob, toDataURL, getContext). */
function makeCanvasLike() {
  return {
    width: 800,
    height: 600,
    toBlob: (cb: (blob: Blob | null) => void) => {
      cb(new Blob(['mock-canvas'], { type: 'image/png' }));
    },
    toDataURL: () =>
      'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
    getContext: () => canvas2dContext,
  };
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
  // ROUND-2: track whether a camera animation is in flight so stop() can model
  // MapLibre reality — stopping an in-flight animation fires moveend
  // SYNCHRONOUSLY (HandlerManager._stop → _afterEase → moveend). flyTo/fitBounds/
  // easeTo start an animation; jumpTo/moveend end it.
  let animating = false;

  // Issue #404 additions: style/rendering state that the previously-closed mock
  // surface never modeled (every one of these used to TypeError when reached).
  const bounds: [number, number, number, number] = options.bounds ?? [
    viewport.center[0] - 0.1,
    viewport.center[1] - 0.1,
    viewport.center[0] + 0.1,
    viewport.center[1] + 0.1,
  ];
  const styleLoaded = options.styleLoaded ?? true;
  const renderedFeatures = options.renderedFeatures ?? [];
  let terrain: { source: string; exaggeration?: number } | null = null;
  let pixelRatio = 1;
  const images = new Set<string>();
  const controls: Array<{ control: unknown; position?: string }> = [];

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
    addControl: [],
    setTerrain: [],
    queryRenderedFeatures: [],
    setLayoutProperty: [],
    setPaintProperty: [],
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
      // Stateful: actually reorder `_layers` (before my previous no-op, z-order
      // assertions silently observed the stale order). No beforeId → end of the
      // array = top of the z-order; beforeId → immediately before that layer.
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) {
        const [moved] = layers.splice(i, 1);
        if (beforeId !== undefined && beforeId !== null) {
          const j = layers.findIndex((l) => l.id === beforeId);
          layers.splice(j >= 0 ? j : layers.length, 0, moved);
        } else {
          layers.push(moved);
        }
      }
      calls.moveLayer.push({ id, beforeId: beforeId ?? null });
    }),
    setFilter: vi.fn((layerId: string, filter: any) => {
      calls.setFilter.push({ layerId, filter });
    }),
    setLayoutProperty: vi.fn((layerId: string, name: string, value: unknown) => {
      // Stateful: write into the target layer def so render assertions can read
      // the value back through getLayoutProperty/getStyle (MapLibre semantics).
      const layer = layers.find((l) => l.id === layerId);
      if (layer) {
        layer.layout = layer.layout ?? {};
        layer.layout[name] = value;
      }
      calls.setLayoutProperty.push({ layerId, name, value });
    }),
    setPaintProperty: vi.fn((layerId: string, name: string, value: unknown) => {
      const layer = layers.find((l) => l.id === layerId);
      if (layer) {
        layer.paint = layer.paint ?? {};
        layer.paint[name] = value;
      }
      calls.setPaintProperty.push({ layerId, name, value });
    }),
    getLayoutProperty: vi.fn((layerId: string, name: string) => {
      const layer = layers.find((l) => l.id === layerId);
      // null when unset (real MapLibre resolves style defaults; the mock keeps
      // the explicit-value contract only — enough for verification logic).
      return layer?.layout?.[name] ?? null;
    }),
    getPaintProperty: vi.fn((layerId: string, name: string) => {
      const layer = layers.find((l) => l.id === layerId);
      return layer?.paint?.[name] ?? null;
    }),

    // ─── Rendering / style-state surface (issue #404) ──────────────────────
    isStyleLoaded: vi.fn(() => styleLoaded),
    getBounds: vi.fn(() => ({
      getWest: () => bounds[0],
      getSouth: () => bounds[1],
      getEast: () => bounds[2],
      getNorth: () => bounds[3],
    })),
    queryRenderedFeatures: vi.fn((geometry: unknown, params?: unknown) => {
      calls.queryRenderedFeatures.push({ geometry, params });
      // Configured features only — no spatial filtering (that is real MapLibre
      // territory; tests drive the result set explicitly).
      const features =
        typeof renderedFeatures === 'function' ? renderedFeatures() : renderedFeatures;
      return Array.isArray(features) ? features : [];
    }),
    getCanvas: vi.fn(() => makeCanvasLike()),
    hasImage: vi.fn((id: string) => images.has(id)),
    removeImage: vi.fn((id: string) => {
      images.delete(id);
    }),
    getPixelRatio: vi.fn(() => pixelRatio),
    setPixelRatio: vi.fn((ratio: number) => {
      pixelRatio = ratio;
    }),
    addControl: vi.fn((control: unknown, position?: string) => {
      controls.push({ control, position });
      calls.addControl.push({ control, position: position ?? null });
      return map;
    }),
    setTerrain: vi.fn((opts: { source: string; exaggeration?: number } | null) => {
      terrain = opts ? { source: opts.source, exaggeration: opts.exaggeration } : null;
      calls.setTerrain.push({ options: opts });
      return map;
    }),

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
    _images: images,
    _terrain: terrain,
    _controls: controls,
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
