/**
 * Render Observation — bounded evidence of what the browser actually renders
 * (P9 render-observed product closure).
 *
 * RenderObservation is an OBSERVATION, not map truth: MapSpec stays the only
 * desired-state authority and the runtime layer registry stays the only
 * imperative mount ledger. This module only answers "what does MapLibre show
 * right now" for the backend Map Product Finalizer:
 *
 *   MapSpec (desired)  ≠  RenderObservation (observed runtime)
 *
 * It wraps the existing `collectCartographicRuntimeObservation` (layer family
 * convergence — no second layer computation) and adds the product-completion
 * dimensions that evidence was missing:
 *
 *   - mapspec_revision   (session-cursor revision; the backend stamps its own
 *                         authoritative revision after the fingerprint gate —
 *                         the client value is diagnostic, never a guard);
 *   - map_idle           (bounded settle: race(map 'idle', SETTLE_TIMEOUT_MS));
 *   - components[]       (shared resolveMapComponents projection + the same
 *                         north/scale fallback rule MapSpecChrome applies —
 *                         O(components), no second layout engine);
 *   - runtime_errors[]   (bounded deduped ring fed by the map 'error' event).
 *
 * Bounded by contract: ids / booleans / small metadata only — never GeoJSON,
 * feature arrays or the full MapSpec (backend DTO enforces the same budget).
 */

import type { Map as MaplibreMap } from 'maplibre-gl';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import { collectCartographicRuntimeObservation } from './runtime-evidence';
import { resolveMapComponents } from '@/lib/map-components/resolve-components';

/** Map 'idle' may never fire (raster churn / animation) — settle is bounded.
 * 400ms：短有界窗口 —— 合并 reconcile 突发、贴近渲染落定，同时不显著
 * 拖慢 observation→repair 回路的每轮往返（task §12/§24：short bounded
 * settle，不得阻塞）。 */
export const RENDER_SETTLE_TIMEOUT_MS = 400;

/** Bounded runtime error ring (deduped by message; oldest evicted). */
export const MAX_RUNTIME_ERRORS = 8;
const MAX_ERROR_MESSAGE = 160;

/** Bounded component observation entries per observation upload. */
export const MAX_OBSERVED_COMPONENTS = 32;

export interface ObservedRuntimeError {
  message: string;
  /** MapLibre error events usually carry the offending layer/source id. */
  target?: string;
}

export interface ObservedComponent {
  id: string;
  type: string;
  enabled: boolean;
  /**
   * True when MapSpecChrome would mount it: enabled, chrome-renderable type,
   * or the built-in north/scale fallback (mirror of map-spec-chrome logic).
   */
  mounted: boolean;
  anchor: string;
  floating: boolean;
  collapsed: boolean;
  /** floating pixel rect (viewport px semantics; undefined for anchored). */
  rect?: { x: number; y: number; width?: number; height?: number };
  /** True when injected by the chrome fallback (not present in the spec). */
  fallback?: boolean;
}

export interface RenderObservation {
  // ── P9 增维 ──
  mapspec_revision: number;
  observed_at: number;
  map_idle: boolean;
  components: ObservedComponent[];
  runtime_errors: ObservedRuntimeError[];
  // ── 既有 runtime evidence（collectCartographicRuntimeObservation）──
  mapspec_fingerprint: string;
  style_loaded: boolean;
  layers: Array<Record<string, unknown>>;
  viewport: Record<string, unknown>;
  reconcile_error: string;
  // raster_image 等额外证据字段由底层采集器携带（有界预算由后端 DTO 把关）
  [key: string]: unknown;
}

/**
 * Chrome-renderable types — mirror of map-panel's CHROME_RENDERABLE_TYPES
 * (the set MapSpecChrome actually mounts). Unknown future types do not become
 * false "component missing" findings.
 */
const CHROME_RENDERABLE_TYPES: ReadonlySet<string> = new Set([
  'title', 'subtitle', 'north_arrow', 'scale_bar', 'attribution',
  'continuous_colorbar', 'legend', 'categorical_legend',
  'annotation', 'statistics_panel', 'chart_panel', 'map_border', 'graticule',
]);

/**
 * Map 'error' events carry `{ error }` (Error with optional layerId/sourceId)
 * or plain values depending on the failure site — extract bounded metadata.
 */
function observeRuntimeError(err: unknown): ObservedRuntimeError {
  const record = (typeof err === 'object' && err !== null ? err : {}) as {
    message?: unknown;
    layerId?: unknown;
    sourceId?: unknown;
    status?: unknown;
  };
  const inner = record.message;
  const message = String(
    typeof inner === 'string' && inner
      ? inner
      : (err instanceof Error ? err.message : String(err ?? 'unknown error')),
  ).slice(0, MAX_ERROR_MESSAGE);
  const target =
    typeof record.layerId === 'string'
      ? record.layerId
      : typeof record.sourceId === 'string'
        ? record.sourceId
        : undefined;
  const observed: ObservedRuntimeError = { message };
  if (target) observed.target = target.slice(0, 64);
  return observed;
}

/**
 * Bounded, deduped error ring. Lives with the owner of the map instance
 * lifecycle (the observation hook registers/cleans it up on unmount and
 * session switch) — never unbounded, never per-frame.
 */
export class RuntimeErrorRing {
  private entries: ObservedRuntimeError[] = [];
  private seen = new Set<string>();

  push(err: unknown): void {
    const observed = observeRuntimeError(err);
    const key = `${observed.target ?? ''}|${observed.message}`;
    if (this.seen.has(key)) return;
    if (this.entries.length >= MAX_RUNTIME_ERRORS) {
      const dropped = this.entries.shift();
      if (dropped) this.seen.delete(`${dropped.target ?? ''}|${dropped.message}`);
    }
    this.entries.push(observed);
    this.seen.add(key);
  }

  /** Errors observed so far; the collector drains (takes) them. */
  drain(): ObservedRuntimeError[] {
    if (this.entries.length === 0) return [];
    const out = this.entries;
    this.entries = [];
    this.seen.clear();
    return out;
  }

  get size(): number {
    return this.entries.length;
  }
}

/**
 * Component observation from the shared resolver — same semantics MapSpecChrome
 * applies when mounting (enabled filter + chrome-renderable set + north/scale
 * fallback). Pure projection of the committed spec, O(components).
 */
export function observeComponents(spec: MapSpec | null | undefined): ObservedComponent[] {
  const resolved = resolveMapComponents(spec ?? null);
  // MapSpecChrome 在无 chrome-renderable 组件时整体不挂载（map-panel
  // hasSpecChrome 门）—— 此时 fallback 装饰也不存在，观察必须如实为空。
  const enabledRenderable = resolved.some(
    (c) => c.enabled && CHROME_RENDERABLE_TYPES.has(c.type),
  );
  if (!resolved.length || !enabledRenderable) return [];
  const out: ObservedComponent[] = [];
  const push = (c: ObservedComponent) => {
    if (out.length >= MAX_OBSERVED_COMPONENTS) return;
    out.push(c);
  };
  const hasType = (t: string) => resolved.some((c) => c.type === t);
  for (const c of resolved) {
    push({
      id: c.id.slice(0, 64),
      type: c.type,
      enabled: c.enabled,
      mounted: c.enabled && CHROME_RENDERABLE_TYPES.has(c.type),
      anchor: c.anchor,
      floating: c.floating,
      collapsed: c.collapsed,
      ...(c.floatingRect
        ? {
            rect: {
              x: c.floatingRect.x,
              y: c.floatingRect.y,
              ...(c.floatingRect.width !== undefined ? { width: c.floatingRect.width } : {}),
              ...(c.floatingRect.height !== undefined ? { height: c.floatingRect.height } : {}),
            },
          }
        : {}),
    });
  }
  // MapSpecChrome injects fallback north/scale when the spec has none — the
  // observation must report what is actually on screen, so mirror the fallback
  // (same rule, no second default table: absent from spec → chrome mounts it).
  if (!hasType('north_arrow')) {
    push({ id: '__fallback_north_arrow', type: 'north_arrow', enabled: true, mounted: true, anchor: 'top-right', floating: false, collapsed: false, fallback: true });
  }
  if (!hasType('scale_bar')) {
    push({ id: '__fallback_scale_bar', type: 'scale_bar', enabled: true, mounted: true, anchor: 'bottom-right', floating: false, collapsed: false, fallback: true });
  }
  return out;
}

/**
 * Bounded settle: resolve when the map reports fully loaded ('idle'-equivalent
 * via `loaded()`) or reaches 'idle', or the timeout elapses — whichever first.
 * Never blocks longer than RENDER_SETTLE_TIMEOUT_MS and never leaks the
 * one-shot listener.
 */
export function waitForRenderSettle(map: MaplibreMap | null | undefined): Promise<boolean> {
  if (!map) return Promise.resolve(false);
  // 已完全落定（全部瓦片/渲染就绪）→ 立即返回，不为已完成的地图付等待。
  if (typeof map.loaded === 'function') {
    try {
      if (map.loaded() === true) return Promise.resolve(true);
    } catch {
      // 探测失败 → 走 idle/超时 race
    }
  }
  if (typeof map.once !== 'function' || typeof map.off !== 'function') {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    let settled = false;
    const done = (idle: boolean) => {
      if (settled) return;
      settled = true;
      map.off('idle', onIdle);
      clearTimeout(timer);
      resolve(idle);
    };
    const onIdle = () => done(true);
    const timer = setTimeout(() => done(false), RENDER_SETTLE_TIMEOUT_MS);
    map.once('idle', onIdle);
  });
}

export interface CollectRenderObservationOptions {
  map: MaplibreMap;
  spec: MapSpec;
  /** HUD rows already consumed by the shared runtime-evidence collector. */
  layers: Parameters<typeof collectCartographicRuntimeObservation>[2];
  mapspecFingerprint: string;
  mapspecRevision: number;
  errorRing: RuntimeErrorRing;
  mapIdle: boolean;
  /** Bounded reconcile failure text (MapSpecRuntime.getLastError()). */
  reconcileError?: string;
  /** Applied spec basis for source-convergence (MapSpecRuntime.getAppliedSpec()). */
  applied?: MapSpec | null;
}

/**
 * One bounded render observation. Reuses collectCartographicRuntimeObservation
 * for layers/viewport/style (single source of runtime evidence); adds revision,
 * idle, components and the drained error ring.
 */
export function collectRenderObservation({
  map,
  spec,
  layers,
  mapspecFingerprint,
  mapspecRevision,
  errorRing,
  mapIdle,
  reconcileError = '',
  applied = null,
}: CollectRenderObservationOptions): RenderObservation {
  const base = collectCartographicRuntimeObservation(
    map,
    spec,
    layers,
    mapspecFingerprint,
    reconcileError,
    applied,
  );
  // 显式构造：既有证据字段逐项定型后落位（spread 仅携带额外证据字段，
  // 随后不被覆盖）。
  return {
    ...base,
    mapspec_fingerprint: mapspecFingerprint,
    style_loaded: base.style_loaded === true,
    layers: Array.isArray(base.layers)
      ? (base.layers as Array<Record<string, unknown>>)
      : [],
    viewport:
      base.viewport && typeof base.viewport === 'object'
        ? (base.viewport as Record<string, unknown>)
        : {},
    reconcile_error:
      typeof base.reconcile_error === 'string' ? base.reconcile_error : '',
    mapspec_revision: mapspecRevision,
    observed_at: Date.now(),
    map_idle: mapIdle,
    components: observeComponents(spec),
    runtime_errors: errorRing.drain(),
  };
}
