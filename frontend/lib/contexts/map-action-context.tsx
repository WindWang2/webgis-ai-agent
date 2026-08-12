'use client';

import React, { createContext, useContext, useState, useCallback, useRef, useMemo } from 'react';
import type { MapActionCorrelation, MapActionPayload, MapActionTerminalStatus } from '@/lib/types';
import type { MapActionAck, MapActionAckSink } from '@/lib/api/map-action-acks';
import { useHudStore } from '@/lib/store/useHudStore';
import { devOnly } from '@/lib/utils/logger';
// 审计 follow-up：原 PR 用 require() 是为了避免 "circular dep"，但 providers.ts
// 是叶子模块（零 import）—— 不存在循环。改为正常 ESM import，避免触发
// @typescript-eslint/no-require-imports（CI Docker 内 next build 严格模式）。
import { TILE_PROVIDERS } from '@/lib/providers';

export type { MapActionPayload };

export interface MapSnapshot {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
  bounds?: [number, number, number, number];
}

/** Terminal details passed by the handler into reportTerminal (design §6). */
export interface MapActionTerminalDetails {
  error?: string;
  /** Actual resulting state, e.g. the settled viewport `{center, zoom, bearing, pitch}`. */
  actual?: unknown;
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
}

/** One completed action kept in the bounded ring for tests/dev (design §6). */
export interface CompletedMapAction {
  action_id: string;
  command: string;
  status: MapActionTerminalStatus;
  error?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number | null;
  correlation?: MapActionCorrelation | null;
  requested?: Record<string, unknown> | null;
  actual?: Record<string, unknown> | null;
  /** Wall-clock time the terminal state was recorded (ISO). */
  terminal_at: string;
}

export interface MapActionContextType {
  actions: MapActionPayload[];
  dispatchAction: (action: MapActionPayload) => void;
  /** Pop the queue head. Optional ``actionId`` guards the pop: it only applies
   * when the current head carries that id (per-action settle guard). */
  popAction: (actionId?: string) => void;
  selectedBaseLayer: number;
  setSelectedBaseLayer: (index: number) => void;
  registerSnapshotFn: (fn: () => MapSnapshot) => void;
  getMapSnapshot: () => MapSnapshot | null;
  // ── V3 (Harness–Map Interaction Closed Loop, design §6) ──
  /** Register a sink for terminal acks (useMapBridge registers the POST sender). Returns an unsubscribe fn. */
  registerAckSink: (fn: MapActionAckSink) => () => void;
  /** Report a terminal state for an action — context wraps it into an ack, rings it, and fans out to sinks. */
  reportTerminal: (
    action: MapActionPayload,
    status: MapActionTerminalStatus,
    details?: MapActionTerminalDetails,
  ) => void;
  /** Mark every pending action cancelled('session_switch') + acked, then empty the queue (session switch). */
  clearActions: () => void;
  /** Actions dropped by the MAX_PENDING_ACTIONS overflow (dropped-oldest-queued), per session. */
  droppedCount: number;
  /** Bounded terminal ring (MAX_COMPLETED_ACTIONS) for tests/dev. */
  completedActions: CompletedMapAction[];
  /** action_id of the queue head — the action the handler is currently running (or null when idle). */
  runningActionId: string | null;
}

export const MapActionContext = createContext<MapActionContextType | undefined>(undefined);

// V3 queue bounds (design §6): overflow drops the oldest QUEUED action (never the
// running head); terminal acks are kept in a bounded ring for tests/dev.
const MAX_PENDING_ACTIONS = 32;
const MAX_COMPLETED_ACTIONS = 100;
// Terminal-id dedup set cap: action ids are globally unique, so only the most
// recent 2000 need remembering for first-terminal-wins (bounded memory).
const MAX_TERMINAL_IDS = 2000;
// Camera commands coalesce: a new one supersedes still-QUEUED camera actions.
const CAMERA_COMMANDS = new Set(['fly_to', 'set_map_view', 'zoom_to_bbox']);

// ACK snapshot (requested/actual) guard — the backend caps each serialized ack
// at 16KB (422 beyond). Heatmap/layer params carry base64 images / inline
// geojson that would blow the cap and drop the WHOLE debounced batch, so:
// 1) drop data-dump keys outright; 2) keep only scalar / array-of-scalars
// values; 3) cap the serialized snapshot at ~2KB (mirrors backend
// `_cap_requested_snapshot` in app/services/tool_dispatch_service.py).
const ACK_DROP_KEYS = new Set(['geojson', 'image', 'features', 'data']);
const ACK_SNAPSHOT_MAX_CHARS = 2048;

function isScalarSnapshotValue(v: unknown): boolean {
  if (v === null || typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return true;
  return Array.isArray(v) && v.every((x) => x === null || ['string', 'number', 'boolean'].includes(typeof x));
}

/** Sanitize an ack `requested`/`actual` snapshot: scalars + arrays of scalars
 * only, data-dump keys dropped, serialized size capped (mirrors backend
 * `_cap_requested_snapshot`). Keeps every ack far under the 16KB per-ack cap. */
function capAckSnapshot(params: Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return null;
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(params)) {
    if (ACK_DROP_KEYS.has(k)) continue;
    const v = params[k];
    if (!isScalarSnapshotValue(v)) continue;
    const probe = { ...out, [k]: v };
    try {
      if (JSON.stringify(probe).length > ACK_SNAPSHOT_MAX_CHARS) break;
    } catch {
      continue; // unserializable value (e.g. cyclic) — skip the key
    }
    out[k] = v;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** Client-side action id fallback when the backend didn't mint one (e.g. bbox-fallback fly_to). */
function mintActionId(): string {
  if (globalThis.crypto?.randomUUID) return `fe-${globalThis.crypto.randomUUID()}`;
  return `fe-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function MapActionProvider({ children }: { children: React.ReactNode }) {
  const [actions, setActions] = useState<MapActionPayload[]>([]);
  // 审计 F34：lazy init 从持久化的 useHudStore.baseLayer name 反查 index，
  // 防刷新后 index 重置为 1 与持久化 name 不一致 -> 底图闪烁。
  const [selectedBaseLayer, setSelectedBaseLayer] = useState<number>(() => {
    try {
      const persistedName = useHudStore.getState().baseLayer;
      // TILE_PROVIDERS 通过顶部 ESM import 引入；providers.ts 是叶子模块无循环依赖。
      const idx = TILE_PROVIDERS.findIndex((p: any) => p.name === persistedName);
      if (idx >= 0) return idx;
      const fallbackIdx = TILE_PROVIDERS.findIndex((p: any) => p.name === 'Carto 深色');
      return fallbackIdx >= 0 ? fallbackIdx : 1;
    } catch {
      return 1;
    }
  });
  const snapshotFnRef = useRef<(() => MapSnapshot) | null>(null);

  // The queue's authoritative source is a ref so dispatch/coalesce/pop operate on
  // the *latest* queue synchronously (batched dispatches from useMapBridge's
  // commands[] loop chain correctly); `actions` state is a render mirror.
  const queueRef = useRef<MapActionPayload[]>([]);
  const [droppedCount, setDroppedCount] = useState(0);
  const [completedActions, setCompletedActions] = useState<CompletedMapAction[]>([]);
  const completedRingRef = useRef<CompletedMapAction[]>([]);
  // First-terminal-wins dedup. A Map (insertion-ordered) so the oldest entry can
  // be evicted when the set exceeds MAX_TERMINAL_IDS (bounded memory, P3).
  const terminalIdsRef = useRef<Map<string, true>>(new Map());
  const ackSinksRef = useRef<Set<MapActionAckSink>>(new Set());

  // Last fly_to tracking for physical throttling.
  // 审计 F21：之前对每个命令都用 JSON.stringify 做去重，问题：
  //   (1) key 顺序不同 → 同义动作漏过；
  //   (2) export_map 等大 payload 每次都要序列化，浪费；
  //   (3) AI 连续两次同义指令被静默丢弃（可能本意是 refresh）。
  // 现在：仅对 fly_to 做 2 秒节流（最常见的中途重复），且只比较 center+zoom；
  // 其他命令直接入队，MapActionHandler 本就顺序消费，不需要前端去重。
  const lastFlyToRef = useRef<{
    centerKey: string;
    zoom: number;
    timestamp: number;
  } | null>(null);

  const setQueue = useCallback((next: MapActionPayload[]) => {
    queueRef.current = next;
    setActions(next);
  }, []);

  // First-terminal-wins per action_id (mirrors the backend's idempotent ack log):
  // supersede/clear mark actions terminal; a late in-flight settle must not
  // double-report the same action. The dedup set is capped (bounded memory).
  const reportTerminal = useCallback((
    action: MapActionPayload,
    status: MapActionTerminalStatus,
    details: MapActionTerminalDetails = {},
  ) => {
    const id = action.action_id;
    if (!id || terminalIdsRef.current.has(id)) return;
    terminalIdsRef.current.set(id, true);
    // Cap the dedup set: keep the most recent MAX_TERMINAL_IDS ids (a Map keeps
    // insertion order, so the oldest id is the first key).
    if (terminalIdsRef.current.size > MAX_TERMINAL_IDS) {
      const oldest = terminalIdsRef.current.keys().next().value;
      if (oldest !== undefined) terminalIdsRef.current.delete(oldest);
    }

    // V3: requested/actual go through capAckSnapshot — heatmap base64 images /
    // inline geojson in params must never blow the backend 16KB per-ack cap
    // (a 422 drops the whole debounced batch). Scalar keys (center/zoom/bbox/
    // confirmed) survive.
    const ack: MapActionAck = {
      action_id: id,
      command: action.command,
      status,
      error: details.error,
      started_at: details.startedAt,
      finished_at: details.finishedAt,
      duration_ms: details.durationMs ?? null,
      correlation: action.correlation ?? null,
      requested: capAckSnapshot(action.params as Record<string, unknown>) ?? {},
      actual: details.actual !== undefined
        ? capAckSnapshot(details.actual as Record<string, unknown>)
        : null,
    };

    const entry: CompletedMapAction = {
      ...ack,
      terminal_at: new Date().toISOString(),
    };
    const ring = [...completedRingRef.current, entry];
    if (ring.length > MAX_COMPLETED_ACTIONS) ring.splice(0, ring.length - MAX_COMPLETED_ACTIONS);
    completedRingRef.current = ring;
    setCompletedActions(ring);

    ackSinksRef.current.forEach((sink) => {
      try {
        sink(ack);
      } catch (e) {
        // A failing sink (e.g. a test spy mid-teardown) must never break the queue.
        devOnly.error('[MapActionContext] ack sink threw:', e);
      }
    });
  }, []);

  const dispatchAction = useCallback((newAction: MapActionPayload) => {
    // Normalize the command to lowercase before any downstream logic so the
    // frontend is tolerant of UPPERCASE backend emissions (BASE_LAYER_CHANGE,
    // REMOVE_LAYER, …). The command catalogue keys are all lowercase; the
    // handler and renderer gate look up by this normalized value. MapActionPayload
    // still carries mixed-case literals at the type level — that's fine, the
    // runtime value is what matters.
    const command = newAction.command.toLowerCase();
    // V3: mint a client action id when the backend didn't (text-JSON path,
    // bbox-fallback fly_to) so every queued action can reach a terminal ack.
    // NOTE: cast needed — `.toLowerCase()` widens the command literal union to
    // `string`; the runtime value is what matters (see normalization above).
    const normalized = {
      ...newAction,
      command,
      action_id: newAction.action_id || mintActionId(),
      issued_at: newAction.issued_at ?? new Date().toISOString(),
    } as MapActionPayload;

    if (normalized.command === 'fly_to' && normalized.params) {
      const center = (normalized.params as Record<string, unknown>).center;
      const zoom = (normalized.params as Record<string, unknown>).zoom;
      if (Array.isArray(center) && typeof zoom === 'number') {
        const centerKey = center.join(',');
        const now = Date.now();
        const last = lastFlyToRef.current;
        if (last &&
            last.centerKey === centerKey &&
            last.zoom === zoom &&
            (now - last.timestamp) < 2000) {
          // 节流：2 秒内同地点+同 zoom 的 fly_to 丢弃。被丢弃的动作必须有终态 ack，
          // 否则后端永远等不到它的 terminal（harness 覆盖率受损）。
          reportTerminal(normalized, 'superseded', { error: 'throttled' });
          return;
        }
        lastFlyToRef.current = { centerKey, zoom, timestamp: now };
      }
    }

    const queue = queueRef.current;

    // Camera coalesce (V3): a new camera command supersedes still-QUEUED camera
    // actions — never the running head — and acks them superseded.
    if (CAMERA_COMMANDS.has(command)) {
      const superseded: MapActionPayload[] = [];
      const kept = queue.filter((a, i) => {
        const isQueuedCamera = i !== 0 && CAMERA_COMMANDS.has(a.command.toLowerCase());
        if (isQueuedCamera) superseded.push(a);
        return !isQueuedCamera;
      });
      if (superseded.length > 0) {
        // Report superseded BEFORE the new action lands so the ring reads in order.
        for (const a of superseded) {
          reportTerminal(a, 'superseded', { error: 'newer_camera_command' });
        }
      }
      setQueue([...kept, normalized]);
      return;
    }

    // MAX_PENDING_ACTIONS overflow: drop the oldest QUEUED action (never the
    // running head) and count it. The dropped action still reaches a terminal
    // ack — a silent drop would leave the backend waiting forever.
    if (queue.length >= MAX_PENDING_ACTIONS) {
      const head = queue[0];
      const rest = queue.slice(1);
      const dropped = rest[0];
      const kept = dropped ? [head, ...rest.slice(1)] : head ? [head] : [];
      setDroppedCount((c) => c + 1);
      if (dropped) {
        reportTerminal(dropped, 'cancelled', { error: 'queue_overflow' });
      }
      setQueue([...kept, normalized]);
      return;
    }

    setQueue([...queue, normalized]);
  }, [reportTerminal, setQueue]);

  // V3: pop only when the queue head is the action that actually settled.
  // Guarding by action_id prevents a double-pop when an effect re-run (e.g. a
  // mid-flight mapInstance identity change) settles the same action twice —
  // a blind `slice(1)` would otherwise drop the NEXT queued action.
  const popAction = useCallback((actionId?: string) => {
    const q = queueRef.current;
    if (actionId !== undefined && q[0]?.action_id !== actionId) return;
    setQueue(q.slice(1));
  }, [setQueue]);

  // V3: session switch — every pending action becomes cancelled('session_switch')
  // + acked, the queue empties, and session-scoped counters reset. The terminal
  // dedup set is intentionally NOT cleared: action ids are globally unique
  // (ma-/fe- uuid), so a late in-flight settle after the switch must stay
  // blocked (first-terminal-wins, mirroring the backend's idempotent ack log).
  const clearActions = useCallback(() => {
    const pending = queueRef.current;
    for (const a of pending) {
      reportTerminal(a, 'cancelled', { error: 'session_switch' });
    }
    setQueue([]);
    setDroppedCount(0);
    // The fly_to throttle window is session-scoped: a fresh session's first
    // fly_to must not be dropped just because the previous session flew there.
    lastFlyToRef.current = null;
    completedRingRef.current = [];
    setCompletedActions([]);
  }, [reportTerminal, setQueue]);

  const registerAckSink = useCallback((fn: MapActionAckSink) => {
    ackSinksRef.current.add(fn);
    return () => {
      ackSinksRef.current.delete(fn);
    };
  }, []);

  const registerSnapshotFn = useCallback((fn: () => MapSnapshot) => {
    snapshotFnRef.current = fn;
  }, []);

  const getMapSnapshot = useCallback((): MapSnapshot | null => {
    return snapshotFnRef.current?.() ?? null;
  }, []);

  const runningActionId = actions[0]?.action_id ?? null;

  // 审计 FE-05：useMemo 包裹 value 避免每次 render 创建新对象引用
  // -> 消费 useMapAction() 的组件不会因 provider re-render 而无谓重渲染。
  const value = useMemo(() => ({
      actions,
      dispatchAction,
      popAction,
      selectedBaseLayer,
      setSelectedBaseLayer,
      registerSnapshotFn,
      getMapSnapshot,
      registerAckSink,
      reportTerminal,
      clearActions,
      droppedCount,
      completedActions,
      runningActionId,
    }), [actions, dispatchAction, popAction, selectedBaseLayer, setSelectedBaseLayer,
        registerSnapshotFn, getMapSnapshot, registerAckSink, reportTerminal,
        clearActions, droppedCount, completedActions, runningActionId]);

  return (
    <MapActionContext.Provider value={value}>
      {children}
    </MapActionContext.Provider>
  );
}

export default MapActionProvider;

export function useMapAction() {
  const context = useContext(MapActionContext);
  if (context === undefined) {
    throw new Error('useMapAction must be used within a MapActionProvider');
  }
  return context;
}
