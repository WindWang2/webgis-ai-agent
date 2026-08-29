'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import type { StepResultPayload } from '@/lib/api/chat';
import type { StepResultEvent } from '@/lib/results/types';
import { useMapBridge } from './useMapBridge';
import { useHudStore } from '@/lib/store/useHudStore';
import { apiFetch } from '@/lib/api/transport';
import { API_BASE } from '@/lib/api/config';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { SSEEvent } from '@/lib/api/chat';
import type { ToolCallEntry, PlanProposalPayload, PlanProposalStatus, SelectedFeatureInfo } from '@/lib/store/hud-types';
import { reportLayerFetchFailure, syncSpecLayersToStore } from '@/lib/session/map-state-restore';
import { commitMapSpecDocument, setMapSpecRevision, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { nextTurn, noteAgentDisplayed } from '@/lib/chat/turn-focus';
import { useToastStore } from '@/components/ui/toast';
import type { AgentPlanState } from '@/lib/types/agent-plan';
import type { MapActionPayload } from '@/lib/types';
import { createMessageIdGenerator } from './use-message-id';
import { TokenBatcher } from './token-batcher';
import { IncrementalThinkParser, parseThink } from './incremental-think';
import { streamExplorerProgress } from '@/lib/api/explorer';
import { getAccessToken, getRefreshToken } from '@/lib/auth/tokenStore';
import type { ExplorerStage, ExplorerStatus } from '@/lib/types/explorer';


import { devOnly } from "@/lib/utils/logger";
import { finalizationUserNotice } from "@/lib/map-product/finalizer";
import { parseAgentRuntime, type AgentRuntime } from "@/lib/agent-runtime";

// #742: stable identity — an inline options object churned send/bridge/
// handleSend identities at token-batch frequency.
const RECONNECT_OPTS = { maxAttempts: 2, baseDelayMs: 500 } as const;


/* ─── FE-4: selection/focus → agent snapshot helpers (design §7) ───
 * The selection snapshot sent inside map_state must stay bounded: the PARENT
 * layer id (never the `${layerId}__${sub}` sublayer id), a stable feature
 * identity, ≤5 scalar key properties, and a bbox when the caller computed
 * one. Raw feature payloads / geometry never enter the prompt path.
 */

// Sublayer separator emitted by the MapSpec runtime (`${layerId}__${sub}`,
// mirrors SUBLAYER_SEP in lib/mapspec-runtime/adapter).
const SUBLAYER_SEP = '__';

// Label-ish property keys surfaced first when bounding selected-feature props
// (mirrors the backend format_selected_feature preference order).
const FEATURE_LABEL_KEYS = ['name', 'title', 'label', 'id', 'OBJECTID'];

// Id-like property keys used as feature identity before falling back to a hash.
const FEATURE_ID_KEYS = ['id', 'OBJECTID', 'fid', 'osm_id', '@id'];

const MAX_SNAPSHOT_PROPS = 5;
const MAX_PROP_VALUE_LEN = 60;

// FIX-3-5: feature identity must stay bounded — a raw multi-KB property (e.g.
// a WKT string) must never ride the prompt path. Truncation keeps the id
// stable (same feature → same prefix), which is all the backend correlation
// needs; the content-hash fallback below is already ≤10 chars.
const MAX_FEATURE_ID_LEN = 64;

function truncateFeatureId(v: string): string {
  return v.length > MAX_FEATURE_ID_LEN ? v.slice(0, MAX_FEATURE_ID_LEN) : v;
}

/**
 * FE-P3-2: chat messages were unbounded (every send appends two entries
 * forever, and every non-token event maps the whole array). Keep the most
 * recent 200 — matching the results registry's bounded philosophy.
 */
const MAX_CHAT_MESSAGES = 200;

type ToolCallStatus = 'completed' | 'failed';

/**
 * FE-P3-3: terminal transition for a ToolCallChain row, matched by tool name
 * (the SSE tool_call payload carries no call id). Mutates messages via the
 * hook's setMessages; must be created inside the hook (closure over it).
 *
 * #608: terminal transitions also stamp completedAt (duration badge) and can
 * carry extra display fields (e.g. hasGeojson when step_result mounts a
 * geojson_ref layer).
 */
function makeToolCallStatusMarker(
  thinkingMsgIdRef: { current: string },
  setMessages: (updater: (prev: any[]) => any[]) => void,
) {
  return (tool: string, status: ToolCallStatus, error?: string, extra?: Partial<ToolCallEntry>): void => {
    if (!tool) return;
    setMessages((prev) => {
      const tid = thinkingMsgIdRef.current;
      const idx = tid ? prev.findIndex((m) => m.id === tid) : -1;
      if (idx === -1) return prev;
      const calls = prev[idx].toolCalls;
      if (!calls || calls.length === 0) return prev;
      let changed = false;
      const next = calls.map((c: ToolCallEntry) => {
        if (c.tool !== tool || c.status !== 'running') return c;
        changed = true;
        return {
          ...c,
          status,
          ...(status === 'failed' && error ? { error } : {}),
          ...(extra ?? {}),
          completedAt: Date.now(),
        };
      });
      if (!changed) return prev;
      const copy = [...prev];
      copy[idx] = { ...prev[idx], toolCalls: next };
      return copy;
    });
  };
}

/**
 * #608: stream-level terminal fallback — when the turn dies (error /
 * task_error / task_cancelled) or ends (done / task_complete) without the
 * per-tool step_result/step_error/step_cancelled ever arriving, every still-
 * running ToolCallChain row would keep its spinner forever. Finalize all
 * remaining running rows of the current thinking message in one pass.
 */
function makeToolCallsFinalizer(
  thinkingMsgIdRef: { current: string },
  setMessages: (updater: (prev: any[]) => any[]) => void,
) {
  return (status: ToolCallStatus, error?: string): void => {
    setMessages((prev) => {
      const tid = thinkingMsgIdRef.current;
      const idx = tid ? prev.findIndex((m) => m.id === tid) : -1;
      if (idx === -1) return prev;
      const calls = prev[idx].toolCalls;
      if (!calls || calls.length === 0) return prev;
      if (!calls.some((c: ToolCallEntry) => c.status === 'running')) return prev;
      const next = calls.map((c: ToolCallEntry) =>
        c.status === 'running'
          ? {
              ...c,
              status,
              ...(status === 'failed' && error ? { error } : {}),
              completedAt: Date.now(),
            }
          : c,
      );
      const copy = [...prev];
      copy[idx] = { ...prev[idx], toolCalls: next };
      return copy;
    });
  };
}

function capMessages<T>(messages: T[]): T[] {
  if (messages.length <= MAX_CHAT_MESSAGES) return messages;
  return messages.slice(messages.length - MAX_CHAT_MESSAGES);
}

/**
 * Resolve the parent project-layer id from a possibly-sublayer id via
 * longest-prefix match against the project's layer ids (`__`-boundary aware,
 * so `poi_schools__fill` attributes to `poi_schools`, never `poi`). Falls back
 * to stripping the trailing `__sub` suffix when the parent is gone (layer
 * removed after the click); already-parent ids pass through unchanged.
 */
export function resolveParentLayerId(
  layerId: string,
  projectLayerIds: readonly string[],
): string {
  if (!layerId) return layerId;
  let best: string | null = null;
  for (const id of projectLayerIds) {
    if (!id) continue;
    if (layerId === id) return id; // already a parent id
    if (layerId.startsWith(id + SUBLAYER_SEP) && (!best || id.length > best.length)) {
      best = id;
    }
  }
  if (best) return best;
  const sep = layerId.lastIndexOf(SUBLAYER_SEP);
  return sep > 0 ? layerId.slice(0, sep) : layerId;
}

/** djb2 → 8 hex chars; stable identity for features without an id property. */
function shortContentHash(input: string): string {
  let h = 5381;
  for (let i = 0; i < input.length; i++) {
    h = ((h << 5) + h + input.charCodeAt(i)) | 0;
  }
  return `h-${(h >>> 0).toString(16).padStart(8, '0')}`;
}

/**
 * Bound a raw properties dict to ≤5 scalar entries: label-ish keys first,
 * then insertion order; strings truncated; objects/arrays (e.g. a `geometry`
 * dump) dropped so no raw feature payload reaches the prompt path.
 */
function boundedKeyProperties(
  properties: Record<string, unknown> | undefined,
): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  if (!properties || typeof properties !== 'object') return out;
  const put = (k: string) => {
    if (Object.keys(out).length >= MAX_SNAPSHOT_PROPS || k in out) return;
    const v = properties[k];
    if (v === null || v === undefined) return;
    if (typeof v === 'string') {
      out[k] = v.length > MAX_PROP_VALUE_LEN ? `${v.slice(0, MAX_PROP_VALUE_LEN - 1)}…` : v;
    } else if (typeof v === 'number' || typeof v === 'boolean') {
      out[k] = v;
    }
  };
  for (const k of FEATURE_LABEL_KEYS) put(k);
  for (const k of Object.keys(properties)) put(k);
  return out;
}

/** Feature identity: explicit featureId → id-like property → content hash. */
function resolveFeatureId(
  sel: SelectedFeatureInfo,
  props: Record<string, string | number | boolean>,
): string | number {
  if (typeof sel.featureId === 'string' && sel.featureId) return truncateFeatureId(sel.featureId);
  if (typeof sel.featureId === 'number') return sel.featureId;
  for (const k of FEATURE_ID_KEYS) {
    const v = sel.properties?.[k];
    if (typeof v === 'string' && v) return truncateFeatureId(v);
    if (typeof v === 'number') return v;
  }
  return shortContentHash(JSON.stringify({ p: props, pt: sel.point }));
}

function validBBox(bbox: unknown): [number, number, number, number] | null {
  return Array.isArray(bbox) &&
    bbox.length === 4 &&
    bbox.every((n) => typeof n === 'number' && Number.isFinite(n))
    ? (bbox as [number, number, number, number])
    : null;
}

/**
 * Build the bounded selected_feature snapshot for map_state. Output shape is
 * the contract consumed by the backend `build_map_state_summary`; every field
 * is small and scalar-only (missing data → null, omitted downstream silently).
 */
export function buildSelectedFeatureSnapshot(
  sel: SelectedFeatureInfo,
  projectLayerIds: readonly string[],
) {
  const properties = boundedKeyProperties(sel.properties);
  return {
    layer_id: resolveParentLayerId(sel.layerId, projectLayerIds),
    layer_name: sel.layerName ?? null,
    ref_id: sel.refId ?? null,
    feature_id: resolveFeatureId(sel, properties),
    point: sel.point,
    bbox: validBBox(sel.bbox),
    properties,
    selected_at: sel.selectedAt,
    // #668: honest approximation flag — LLM must not treat tile geometry as source truth
    // Wire canonical is snake_case only; internal SelectedFeatureInfo stays camelCase (store convention)
    is_approximate: sel.isApproximate === true ? true : undefined,
  };
}

function extractEventSessionId(data: unknown): string | undefined {
  if (typeof data === 'object' && data !== null && typeof (data as Record<string, unknown>).session_id === 'string') {
    return (data as Record<string, unknown>).session_id as string;
  }
  return undefined;
}

// stage → 进行时状态（geocode/validate 需去 e，不能裸拼 +ing）
const EXPLORER_ACTIVE_STATUS: Record<string, ExplorerStatus> = {
  discover: 'discovering',
  fetch: 'fetching',
  parse: 'parsing',
  geocode: 'geocoding',
  validate: 'validating',
};

/**
 * #518: 将一条 explorer_progress 事件（后端 orchestrator.stream_progress
 * 的 ExplorerPerceptionEvent 形状：task_id / stage / status / context）应用到
 * explorerTasks store。聊天流 handler 与独立 /explorer/stream/{task_id}
 * 消费者共用同一归一化逻辑，保证两条到达路径产生一致的 UI 状态。
 */
export function applyExplorerProgressToStore(
  data: Record<string, unknown> | undefined | null,
): void {
  if (!data || typeof data !== 'object') return;
  const taskId = data.task_id as string;
  if (typeof taskId !== 'string' || !taskId) return;
  const rawStage = typeof data.stage === 'string' ? data.stage : 'pending';
  const stage = (rawStage === 'pending' ? 'discover' : rawStage) as ExplorerStage;
  const status = data.status as string;
  const context = (data.context as Record<string, unknown>) || {};
  const nextStatus: ExplorerStatus =
    status === 'completed'
      ? 'completed'
      : status === 'failed'
        ? 'failed'
        : status === 'decision_point'
          ? 'decision_required'
          : rawStage === 'pending'
            ? 'idle'
            : (EXPLORER_ACTIVE_STATUS[rawStage] ?? 'idle');
  const progress = (context?.progress as number) || 0;
  const store = useHudStore.getState();
  if (!store.explorerTasks.some((tk) => tk.taskId === taskId)) {
    store.addExplorerTask({
      taskId,
      status: nextStatus,
      stage,
      progress,
      query:
        (typeof context.query === 'string' && context.query) ||
        `深度探索 ${taskId.slice(0, 8)}`,
      startedAt: Date.now(),
      updatedAt: Date.now(),
    });
  } else {
    store.updateExplorerTask(taskId, {
      stage,
      status: nextStatus,
      progress,
    });
  }
}

export function useSSEStream(
  sessionId: string | undefined,
  setSessionId: (sid: string) => void,
  sessionIdRef: React.MutableRefObject<string | undefined>,
  dispatchAction: (act: MapActionPayload) => void,
  getMapSnapshot: () => any,
  userLocation: { lng: number; lat: number; accuracy?: number } | null,
  sessionTokenRef: React.MutableRefObject<string | null>,
  rememberSessionToken?: (sessionId: string, token: string) => void,
  getSessionToken?: (sessionId: string) => string | null,
  /**
   * #1048: session_plan_* 增量的出口。三个事件名在本 hook 的既有分发链中
   * 识别（plan_* 分支保持不动），载荷原样转交；信封关联与状态应用在
   * useSessionPlan（page.tsx 接线）。可选项：既有调用方与测试不受影响。
   * 必须传稳定引用（useSessionPlan 返回的 applySessionPlanEvent），
   * 否则 onEvent 身份抖动会打断在飞流。
   */
  onSessionPlanEvent?: (eventName: string, data: Record<string, unknown>) => void,
) {
  const [messages, setMessages] = useState<
    Array<{
      id: string;
      role: 'user' | 'assistant';
      content: string;
      timestamp: Date | number | null;
      isThinking?: boolean;
      think?: string;
      charts?: unknown[];
      toolCalls?: ToolCallEntry[];
      plan?: PlanProposalPayload;
      agentPlan?: AgentPlanState;
      layerAdded?: string;
      /** Result Workbench linkage (id of the captured AnalysisResult for this step). */
      resultId?: string;
    }>
  >([
    {
      id: '1',
      role: 'assistant',
      content:
        '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI覆盖率',
      timestamp: null,
    },
  ]);

  const [agentRuntime, setAgentRuntime] = useState<AgentRuntime | null>(null);

  const thinkingMsgIdRef = useRef<string>('');
  // FE-P3-3: ToolCallChain terminal transitions (completed on step_result,
  // failed on step_error/step_cancelled).
  const markToolCallStatus = useRef(
    makeToolCallStatusMarker(thinkingMsgIdRef, setMessages),
  ).current; // stable identity: created once per hook instance
  // #608: stream-level terminal fallback for still-running rows.
  const finalizeToolCalls = useRef(
    makeToolCallsFinalizer(thinkingMsgIdRef, setMessages),
  ).current;
  const msgIdGen = useRef(createMessageIdGenerator());
  const layerFetchAbortRef = useRef<AbortController | null>(null);
  // #518: 独立 /explorer/stream/{task_id} 消费者。deep_explore 返回的探索
  // 任务在后台跑数分钟，聊天 SSE 在 done 后即关闭——进度必须经独立流推送。
  // 会话切换/卸载时 abort 全部在飞流；同一 task_id 只开一条流。
  const explorerAbortRef = useRef<AbortController | null>(null);
  const explorerStreamsRef = useRef<Set<string>>(new Set());

  // D-F7 / F-FE-4: incremental think-block tracking. The batcher still
  // delivers full snapshots per flush, but instead of re-parsing the whole
  // accumulated content each time (O(n²) over a turn) the parser scans only
  // the delta since the last flush and carries the <think>/</think> state.
  // reset() is called per turn, alongside the batcher's.
  const thinkParserRef = useRef<IncrementalThinkParser | null>(null);
  if (thinkParserRef.current === null) {
    thinkParserRef.current = new IncrementalThinkParser();
  }

  // Transport goal §21 / F-FE-1 / D-F8: coalesce token chunks into at most one
  // setMessages per animation frame instead of one per token. The batcher owns
  // the accumulated content/reasoning (snapshot semantics, like the prior
  // rawContentRef) and fires onFlush on rAF; onFlush applies the snapshot with
  // a single setMessages. Created once; reset() is called per turn.
  const tokenBatcherRef = useRef<TokenBatcher | null>(null);
  if (tokenBatcherRef.current === null) {
    const hasRaf =
      typeof window !== "undefined" &&
      typeof window.requestAnimationFrame === "function";
    const schedule = hasRaf
      ? (cb: () => void) => window.requestAnimationFrame(cb)
      : (cb: () => void) => window.setTimeout(() => cb(), 16) as unknown as number;
    const cancel = hasRaf
      ? (id: number) => window.cancelAnimationFrame(id)
      : (id: number) => window.clearTimeout(id);
    tokenBatcherRef.current = new TokenBatcher({ schedule, cancel }, (snapshot) => {
      const thinkingId = thinkingMsgIdRef.current;
      if (!thinkingId) return;
      const parser = thinkParserRef.current;
      parser?.append(snapshot.content.slice(parser.consumedLength));
      const parsed = parser?.getResult() ?? parseThink(snapshot.content);
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === thinkingId);
        if (idx === -1) return prev;
        const target = prev[idx];
        const updated = [...prev];
        updated[idx] = {
          ...target,
          content: parsed.content,
          think: parsed.thinking || snapshot.reasoning || target.think,
          isThinking: false,
        };
        return updated;
      });
    });
  }

  // Reset abort controller on session change to cancel in-flight layer fetches
  useEffect(() => {
    if (layerFetchAbortRef.current) {
      layerFetchAbortRef.current.abort();
    }
    layerFetchAbortRef.current = new AbortController();
    return () => {
      layerFetchAbortRef.current?.abort();
    };
  }, [sessionId]);

  // #518: 会话切换/卸载时终止独立 explorer 进度流（任务归属随会话）。
  useEffect(() => {
    if (explorerAbortRef.current) {
      explorerAbortRef.current.abort();
    }
    explorerAbortRef.current = new AbortController();
    explorerStreamsRef.current.clear();
    return () => {
      explorerAbortRef.current?.abort();
    };
  }, [sessionId]);

  // #518: 深度探索任务在后台跑数分钟，聊天 SSE 连接在 done 后关闭，进度
  // 必须经独立 /explorer/stream/{task_id}（owner-verified）推送到同一个
  // explorerTasks store。deep_explore 返回 explorer_task 结果时启动。
  // 匿名会话无 Bearer → 独立流端点 401 不可达，其进度由后端 post-turn
  // 聊天流桥接（bridge_session_explorer_progress）推送；此处跳过独立流，
  // 避免 401 噪音 —— 聊天流 handler 与独立流消费者共用同一归一化逻辑。
  const startExplorerProgressStream = useCallback((taskId: string) => {
    if (explorerStreamsRef.current.has(taskId)) return;
    // 已登录会话（有 access 或 refresh token）走 owner-verified 独立流；
    // 匿名（两者皆无）依赖聊天流桥接。
    if (!getAccessToken() && !getRefreshToken()) return;
    explorerStreamsRef.current.add(taskId);
    const signal = explorerAbortRef.current?.signal;
    (async () => {
      try {
        for await (const ev of streamExplorerProgress(taskId, signal)) {
          if (ev.event === 'explorer_progress' && ev.data && typeof ev.data === 'object') {
            applyExplorerProgressToStore(ev.data as Record<string, unknown>);
            // 终态后释放 per-task 槽位：未来可重开流（断线恢复），且不再去重拦截。
            const status = (ev.data as Record<string, unknown>).status;
            if (status === 'completed' || status === 'failed') {
              explorerStreamsRef.current.delete(taskId);
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        devOnly.warn('[useSSEStream] explorer progress stream failed:', err);
      }
    })();
  }, []);

  const onEvent = useCallback(
    (event: SSEEvent) => {
      const data = event.data as any;

      // INV-2: An event from another session must never mutate the active session or flip session state.
      const eventSid = extractEventSessionId(data);
      if (eventSid && sessionIdRef.current && eventSid !== sessionIdRef.current) {
        devOnly.warn('[useSSEStream] ignored cross-session SSE event:', eventSid);
        return;
      }

      // Session ID assignment (first response binds the initially undefined session)
      if (eventSid && !sessionIdRef.current) {
        setSessionId(eventSid);
        sessionIdRef.current = eventSid;
        setMapSpecSessionCursor(eventSid, 0, sessionTokenRef.current);
      }
      const incomingRevision = data?.mutation_revision ?? data?.result?.mutation_revision;
      if (typeof incomingRevision === 'number' && sessionIdRef.current) {
        setMapSpecRevision(incomingRevision);
      }
      const incomingMapSpec = data?.mapspec ?? data?.result?.mapspec;
      if (incomingMapSpec) {
        // 携带 revision 提交：迟到的旧代次 SSE 事件（HTTP 响应已推进游标）
        // 不再把 committed spec 拉回旧代（ST-P3-1）。
        commitMapSpecDocument(
          incomingMapSpec,
          typeof incomingRevision === 'number' ? incomingRevision : undefined,
        );
        // product-* 等后端直写图层只落 MapSpec 不走 addLayer 路径——镜像
        // 成 HUD 行，图层面板可见、ref 定向显隐可解析（幂等，见注释）。
        syncSpecLayersToStore(incomingMapSpec, sessionIdRef.current);
      }

      // SEC-08：服务端在新建匿名会话时签发 owner_token（随 task_start / session 事件下发）。
      // 前端持有后在后续请求的 X-Session-Token 头里回传。认证会话不携带该字段。
      if (event.event === 'task_start') {
        const runtime = parseAgentRuntime(
          typeof data === 'object' && data !== null
            ? (data as Record<string, unknown>).agent_runtime
            : undefined,
        );
        if (runtime) setAgentRuntime(runtime);
      }

      if (data?.owner_token && typeof data.owner_token === 'string') {
        const ownerSessionId = data.session_id ?? sessionIdRef.current;
        if (typeof ownerSessionId === 'string' && ownerSessionId) {
          rememberSessionToken?.(ownerSessionId, data.owner_token);
        } else {
          sessionTokenRef.current = data.owner_token;
        }
      }

      const thinkingId = thinkingMsgIdRef.current;

      const isTokenLike = event.event === "token" || event.event === "content";
      if (!isTokenLike) {
        // Apply any pending batched tokens before a non-token event (status
        // change, layer add, plan, error) so the final streamed text lands
        // first. No-op when nothing is pending.
        tokenBatcherRef.current?.flush();
      }
      if (isTokenLike) {
        const chunk = data.content || "";
        tokenBatcherRef.current?.push(
          chunk,
          !!(data.is_reasoning || data.type === "reasoning"),
        );
      } else if (event.event === 'tool_call') {
        // Result Workbench: stash the tool-call args so the matching step_result
        // can show truthful input evidence + parameters (best-effort, keyed by
        // tool name within the turn).
        if (data.name && typeof data.arguments === 'string') {
          useHudStore.getState().captureToolCallArgs(data.name, data.arguments);
        }
        // FE-P3-3: populate the thinking message's ToolCallChain — the UI
        // (chat-tab) and the step_cancelled marking existed, but no
        // production event ever wrote msg.toolCalls, so the chain never
        // rendered. The SSE tool_call payload carries no id; use an ordinal
        // id and match terminal transitions by tool name.
        const toolName = typeof data.name === 'string' ? data.name : '';
        if (toolName) {
          setMessages((prev) => {
            const tid = thinkingMsgIdRef.current;
            const idx = tid ? prev.findIndex((m) => m.id === tid) : -1;
            if (idx === -1) return prev;
            const existing = prev[idx].toolCalls ?? [];
            const next = [...existing, {
              id: `tc-${existing.length + 1}`,
              tool: toolName,
              arguments: typeof data.arguments === 'string' ? data.arguments : undefined,
              status: 'running' as const,
              startedAt: Date.now(),
            }];
            const copy = [...prev];
            copy[idx] = { ...prev[idx], toolCalls: next };
            return copy;
          });
        }
      } else if (event.event === "step_result") {
        // #1009: 分支内收窄到 step_result 最小契约（字段漂移由 tsc 捕获）
        const data = event.data as StepResultPayload;
        // Result Workbench: normalize + record this result into the bounded,
        // session-scoped registry. Runs before the layer/chart handling so the
        // result is inspectable even when no layer is mounted. propose_plan and
        // other non-analysis events are ignored inside the slice. The returned
        // id lets the chat layer-added chip deep-link to the same result.
        // captureStepResult 的 StepResultEvent 是本契约的子集投影（历史
        // 三处定义收敛前的边界）；字段级契约由 StepResultPayload 保证。
        const workbenchResultId = useHudStore.getState().captureStepResult(
          data as unknown as StepResultEvent,
        );
        // FE-P3-3: terminal transition for the ToolCallChain row (matched by
        // tool name — the SSE payload carries no call id). #608: stamp
        // completedAt (duration badge) and hasGeojson when the result mounts
        // a geojson_ref layer.
        markToolCallStatus(String(data.tool ?? ''), 'completed', undefined, {
          ...(data.geojson_ref ? { hasGeojson: true, layerId: String(data.geojson_ref) } : {}),
          result: data.result,
        });
        // Plan Mode：propose_plan 返回的 plan 摘要挂到当前消息，由 PlanProposalCard 渲染
        if (data.tool === 'propose_plan' && data.result?.success && data.result?.plan_id) {
          // 守卫已确认 plan 字段在载荷中（运行时契约）；TS 无法跨 index-
          // signature 窄化，边界处显式断言。
          const planResult = data.result as {
            plan_id: string; title: string; summary?: string;
            step_count?: number; destructive_steps?: string[];
            steps_preview?: PlanProposalPayload['steps_preview'];
          };
          const plan: PlanProposalPayload = {
            plan_id: planResult.plan_id,
            title: planResult.title,
            summary: planResult.summary,
            step_count: planResult.step_count ?? 0,
            destructive_steps: planResult.destructive_steps ?? [],
            steps_preview: planResult.steps_preview ?? [],
            status: 'pending',
          };
          setMessages((prev) => prev.map((m) => (m.id === thinkingId ? { ...m, plan } : m)));
        }
        // #518: deep_explore 返回 explorer_task —— 任务在后台跑数分钟，聊天流
        // 无法覆盖全生命周期。启动独立 /explorer/stream/{task_id} 消费进度。
        if (
          data.result?.type === 'explorer_task'
          && typeof data.result?.task_id === 'string'
          && data.result.task_id
        ) {
          startExplorerProgressStream(data.result.task_id);
        }
        // Layer auto-mount — hidden by default; AI calls display_layer to show final results
        if (data.geojson_ref || data.result?.image) {
          // Use geojson_ref as layer ID so AI can reference layers by their ref_id directly
          const layerId = data.geojson_ref ?? `layer-${Date.now()}`;
          const layerName =
            data.tool === 'search_poi'
              ? `搜索结果: ${data.name || 'POI'}`
              : data.tool === 'heatmap_data'
              ? '热力图分析'
              : `分析结果: ${data.tool}`;
          const accentColor = useHudStore.getState().accentColor;
          const legendSpec = data.result?.legend_spec ?? undefined;
          const runtimePatch = data.result?.runtime_patch;
          const patchVisible = typeof runtimePatch?.visible === 'boolean'
            ? runtimePatch.visible
            : !data.geojson_ref;
          const patchOpacity = typeof runtimePatch?.opacity === 'number'
            && Number.isFinite(runtimePatch.opacity)
            ? runtimePatch.opacity
            : 1;
          const patchLegend = runtimePatch?.legend_spec ?? legendSpec;
          const patchStyle = runtimePatch?.style && typeof runtimePatch.style === 'object'
            ? runtimePatch.style
            : { color: accentColor };
          const mapspecGenerationAt = runtimePatch ? Date.now() : undefined;
          const layerMetaTitle: string | null = data.result?.layer_meta?.title ?? null;
          // Detect native heatmap
          const isNativeHeatmap =
            data.tool === 'heatmap_data' &&
            (data.result?.command === 'add_native_heatmap' ||
              data.result?.metadata?.render_type === 'native');
          
          // V3 Performance: Extract descriptor from SSE payload (attached by execution_engine.py)
          const descriptor = data.ref_descriptor;
          
          useHudStore.getState().addLayer({
            id: layerId,
            name: layerName,
            type: data.result?.image ? 'heatmap' : isNativeHeatmap ? 'heatmap' : 'vector',
            visible: patchVisible,
            opacity: patchOpacity,
            group: 'analysis',
            source: data.geojson_ref
              ? ({
                  type: 'FeatureCollection',
                  features: [],
                  metadata: { ref_id: data.geojson_ref },
                } as any)
              : data.result,
            style: patchStyle,
            _refId: data.geojson_ref ?? runtimePatch?.image_ref,
            // Data Plane: 大要素 ref 图层由 MVT 瓦片端点显示（替代整包 GeoJSON）。
            _tileUrl: data.geojson_ref
              ? `${API_BASE}/api/v1/layers/data/${data.geojson_ref}/tiles/{z}/{x}/{y}.mvt?session_id=${sessionIdRef.current}`
              : undefined,
            _descriptor: descriptor,
            legend_spec: patchLegend,
            _mapspecFingerprint: runtimePatch?.mapspec_fingerprint,
            _mapspecLayerId: runtimePatch?.layer_id,
            _mapspecGenerationAt: mapspecGenerationAt,
            _mapspecProjectionFingerprint: runtimePatch?.projection_fingerprint,
            _cartographicRepairs: Array.isArray(runtimePatch?.repair_attempts)
              ? runtimePatch!.repair_attempts.slice(0, 2)
              : undefined,
          });
          // A GIS result is often auto-mounted hidden before the agent authors
          // its final MapSpec.  In that case addLayer is intentionally a no-op;
          // update the existing ref layer with the reviewed presentation and
          // generation instead of creating a duplicate layer.
          if (runtimePatch && data.geojson_ref) {
            useHudStore.getState().updateLayer(layerId, {
              // 命名不得回退成 "分析结果: result-chatcmpl-tool-<hash>"——
              // 用户在图层面板认不出哪行是 POI/热力（2026-08-25 会话回归）。
              // 语义链：layer_meta 标题 → 工具语义名（"搜索结果: 小学"等）。
              name: layerMetaTitle || layerName,
              visible: patchVisible,
              opacity: patchOpacity,
              style: patchStyle,
              legend_spec: patchLegend,
              _refId: data.geojson_ref,
              _descriptor: descriptor,
              _mapspecFingerprint: runtimePatch.mapspec_fingerprint,
              _mapspecLayerId: runtimePatch.layer_id,
              _mapspecGenerationAt: mapspecGenerationAt,
              _mapspecProjectionFingerprint: runtimePatch.projection_fingerprint,
              _cartographicRepairs: Array.isArray(runtimePatch.repair_attempts)
                ? runtimePatch.repair_attempts.slice(0, 2)
                : undefined,
            });
          }
          // 「地图随对话」：runtime_patch 声明 visible（agent 的展示意图，
          // 含热力图等自动挂载可见路径）→ 标记当前轮并收起旧轮可见层。
          if (patchVisible) {
            noteAgentDisplayed(layerId);
          }
          if (layerMetaTitle) {
            useHudStore.getState().setCartographyTitle(layerMetaTitle);
          }

          // V3 Performance: Decide GeoJSON vs MVT based on descriptor metadata.
          // Only fetch full GeoJSON if:
          //   (a) no descriptor available (pre-V3 ref, or Pi path cache miss — safe fallback), OR
          //   (b) not MVT-capable (raster, GeometryCollection-only, etc.), OR
          //   (c) feature_count is at/below the threshold (inline GeoJSON is fine).
          // Large tile-capable layers skip the full download entirely.
          const VECTOR_TILE_THRESHOLD = 5000;
          const shouldFetchFullFC =
            !descriptor ||
            !descriptor.mvt_capable ||
            descriptor.feature_count <= VECTOR_TILE_THRESHOLD;
          
          if (data.geojson_ref && shouldFetchFullFC) {
            const sid = sessionIdRef.current;
            const fetchRef = data.geojson_ref;
            // SEC-08：匿名会话的图层引用数据受 owner_token 保护。
            const token = sessionTokenRef.current;
            apiFetch<GeoJSONFeatureCollection>(
              `/api/v1/layers/data/${encodeURIComponent(fetchRef)}?session_id=${encodeURIComponent(sid ?? '')}`,
              {
                signal: layerFetchAbortRef.current?.signal,
                ownerToken: token,
                timeoutMs: 120_000,
                label: 'Layer data error',
              }
            )
              .then((geojson) => {
                if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                  // Guard: only write if the layer still exists with this ref (not removed and re-added with different data)
                  const current = useHudStore.getState().layers.find((l) => l.id === fetchRef);
                  if (current && current._refId === fetchRef) {
                    useHudStore.getState().updateLayer(fetchRef, { source: geojson });
                    // Store-mounted add_layer never reaches the handler flyTo.
                    // Frame the fetched features so POIs are not a 1px spec
                    // on the default China view.
                    useHudStore.getState().focusLayer(fetchRef);
                  }
                }
              })
              .catch((err) => {
                // transport 约定：调用方主动 abort 以原生 AbortError 直通（会话切换/
                // 组件卸载会 abort 本 fetch）。这是预期控制流，不是错误，不进 console。
                reportLayerFetchFailure(
                  '[LiveLayerFetch] Failed to fetch geojson_ref:',
                  layerName,
                  err,
                );
              });
          }

          setMessages((prev) =>
            prev.map((m) => (m.id === thinkingId ? { ...m, layerAdded: layerName, resultId: workbenchResultId } : m))
          );
        }
        // Chart data from generate_chart tool — attach to message for rendering in chat
        if (data.result?.chart) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === thinkingId
                ? {
                    ...m,
                    // FE-P3-2: bounded per-message chart history.
                    charts: [...((m.charts as any[]) ?? []).slice(-19), data.result?.chart],
                  }
                : m
            )
          );
        }
      } else if (event.event === 'plan_ready') {
        try {
          const incoming = data;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === thinkingId
                ? {
                    ...m,
                    agentPlan: {
                      intent: incoming.intent,
                      domains: incoming.domains ?? [],
                      steps: (incoming.steps ?? []).map((s: any) => ({
                        n: s.n,
                        goal: s.goal,
                        tool_family: s.tool_family,
                        // #615: restored plans carry done:bool per step
                        // (backend P3-2/P2-6) — map it to the status the
                        // PlanCard renders, don't hardcode pending.
                        status: s.done ? ('done' as const) : ('pending' as const),
                      })),
                      finalized: false,
                    },
                  }
                : m
            )
          );
        } catch (err) {
          devOnly.warn('[plan_ready] parse failed', err);
        }
      } else if (event.event === 'plan_step_done') {
        try {
          const stepN = data.step_n;
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== thinkingId || !m.agentPlan) return m;
              return {
                ...m,
                agentPlan: {
                  ...m.agentPlan,
                  steps: m.agentPlan.steps.map((s) =>
                    s.n === stepN ? { ...s, status: 'done' as const } : s
                  ),
                },
              };
            })
          );
        } catch (err) {
          devOnly.warn('[plan_step_done] parse failed', err);
        }
      } else if (event.event === 'plan_finalized') {
        try {
          const skipped = new Set<number>(data.skipped ?? []);
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== thinkingId || !m.agentPlan) return m;
              return {
                ...m,
                agentPlan: {
                  ...m.agentPlan,
                  finalized: true,
                  steps: m.agentPlan.steps.map((s) =>
                    skipped.has(s.n) ? { ...s, status: 'skipped' as const } : s
                  ),
                },
              };
            })
          );
        } catch (err) {
          devOnly.warn('[plan_finalized] parse failed', err);
        }
      } else if (
        event.event === 'session_plan_updated' ||
        event.event === 'session_plan_progress' ||
        event.event === 'session_plan_superseded'
      ) {
        // #1048: SessionPlan 实时增量（Pi 路径，与上方 plan_* 是两个计划概念，
        // ADR-0076）。载荷是冻结的线上投影（session_plan.py 构造），本 hook 只
        // 在既有分发链里识别三个事件名并原样转交；信封关联与状态应用在
        // useSessionPlan。跨会话事件已被本函数顶部的 INV-2 守卫丢弃。
        onSessionPlanEvent?.(event.event, data as Record<string, unknown>);
      } else if (event.event === 'map_finalization') {
        // ADR-0081：后端 Completion Runtime 的完成态披露（拼接在
        // tool_execution_end 后或 turn 收尾独立下发）。视口真相在前端 ——
        // 派发 MAP_FINALIZATION 命令做一次有界校验/修复（相交不动相机、
        // 不相交 fitBounds 一次、空结果 no-op）；用户可见披露仅在异常态。
        const payload = (data ?? {}) as {
          status?: string;
          result_bbox?: number[];
        };
        dispatchAction({
          command: 'MAP_FINALIZATION',
          params: {
            status: String(payload.status ?? 'pending'),
            bbox: Array.isArray(payload.result_bbox)
              ? (payload.result_bbox as [number, number, number, number])
              : undefined,
          },
        });
        const notice = finalizationUserNotice(
          payload as Parameters<typeof finalizationUserNotice>[0],
        );
        if (notice) {
          devOnly.warn('[MapFinalization]', notice);
        }
      } else if (event.event === 'step_cancelled') {
        // B-P2: 步骤被抢占取消时后端下发 step_cancelled
        // ({task_id, step_id, tool, session_id})。把对应 running 的 tool-call
        // 行标记为已取消（复用 ToolCallEntry 的 failed 形态），否则该行会一直
        // 停在 running 直到流结束。已到终态的行绝不覆盖；未匹配时原样返回 prev，
        // 保持 message 对象身份不变。
        // #608: 前端行 id 是自造 tc-N、后端 step_id 是 step-{n} 两个永不
        // 相交的 id 空间——按 step_id 匹配是死分支。改按工具名匹配（与
        // step_result 的终态匹配一致），行 id 只用于 React key/aria。
        const tool = data.tool;
        // R2F-2: the cancelled call will never emit its step_result — drop its
        // queued args so the retry's step_result pairs with the retry's args.
        if (typeof tool === 'string' && tool) {
          useHudStore.getState().discardPendingToolArgs(tool);
          setMessages((prev) => {
            const msgIdx = prev.findIndex(
              (m) => m.id === thinkingId && Array.isArray(m.toolCalls),
            );
            if (msgIdx === -1) return prev;
            const toolCalls = prev[msgIdx].toolCalls;
            if (!toolCalls || toolCalls.length === 0) return prev;
            let changed = false;
            const nextCalls = toolCalls.map((c) => {
              const matches = c.tool === tool && c.status === 'running';
              if (!matches) return c;
              changed = true;
              return { ...c, status: 'failed' as const, error: '已取消', completedAt: Date.now() };
            });
            if (!changed) return prev;
            const next = [...prev];
            next[msgIdx] = { ...prev[msgIdx], toolCalls: nextCalls };
            return next;
          });
        }
      } else if (event.event === 'task_cancelled') {
        // #466: the cancelled task's tool calls never emit their
        // step_result/step_cancelled — their queued args must not leak into
        // the next turn's workbench evidence.
        useHudStore.getState().resetPendingToolArgs();
        // #608: 抢占取消同样不会给每条 tool-call 发 step_cancelled —— 兜底把
        // 全部 running 行终结为已取消，否则 spinner 永远旋转。
        finalizeToolCalls('failed', '已取消');
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== thinkingId) return m;
            const existing = m.content && !m.isThinking ? m.content : "";
            return {
              ...m,
              content: existing ? `${existing}\n\n⏹️ [已取消]` : "⏹️ [已取消]",
              isThinking: false,
            };
          }),
        );
      } else if (
        event.event === 'error' ||
        event.event === 'step_error' ||
        event.event === 'task_error'
      ) {
        // B-P2-13: previously any error/step_error/task_error replaced the
        // ENTIRE message with a generic string, discarding whatever had
        // already streamed and the server's real error detail. Preserve the
        // partial answer and append the actual error (or a fallback note).
        // R2F-2: a failed call never emits its step_result — drop its queued
        // args so the retry's step_result pairs with the retry's args.
        if (event.event === 'step_error' && typeof data?.tool === 'string' && data.tool) {
          useHudStore.getState().discardPendingToolArgs(data.tool);
          markToolCallStatus(data.tool, 'failed', typeof data?.error === 'string' ? data.error : undefined);
        } else if (event.event === 'error' || event.event === 'task_error') {
          // #466: a stream-level death ends the turn — remaining queued args
          // have no step_result coming and must not leak into the next turn.
          useHudStore.getState().resetPendingToolArgs();
        }
        const raw = data?.error;
        const detail =
          typeof raw === "string" && raw.trim()
            ? raw
            : event.event === "step_error"
              ? "工具执行失败。"
              : "请求失败，请重试。";
        // #608: 流级死亡（error/task_error）不会为每条 in-flight 工具补发
        // step_error/step_cancelled —— 兜底终结全部 running 行，spinner 不再
        // 永久旋转。
        if (event.event === 'error' || event.event === 'task_error') {
          finalizeToolCalls('failed', detail);
        }
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== thinkingId) return m;
            const existing = m.content && !m.isThinking ? m.content : "";
            return {
              ...m,
              content: existing ? `${existing}\n\n⚠️ ${detail}` : `⚠️ ${detail}`,
              isThinking: false,
            };
          }),
        );
      } else if (event.event === 'done' || event.event === 'task_complete') {
        // #518: 聊天流 post-turn 桥接（匿名 deep_explore）在 done 之后连接
        // 仍保持打开最多 600s（explorer 进度推送）—— handleSend 的 await
        // 要等连接关闭才返回，isThinking 必须在终态事件到达时就翻转，
        // 否则已完成的回答一直藏在 ThinkingDots 后面。幂等：随后 handleSend
        // 的后置翻转只处理仍为 isThinking 的消息。
        // #608: 正常收官时所有工具行都已有 step_result/step_error 终态；
        // 若有残留 running 行（结果丢失），兜底终结，避免 spinner 永转。
        finalizeToolCalls('failed', '未收到执行结果');
        if (thinkingId) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === thinkingId && m.isThinking ? { ...m, isThinking: false } : m
            )
          );
        }
      } else if (event.event === 'tool_result') {
        // Legacy engine emits tool_result after step_result; Pi path may omit
        // it. Mark a still-running matching row completed if step_result
        // never arrived. Already-terminal rows are left untouched.
        const name = typeof data?.name === 'string' ? data.name : '';
        if (name) markToolCallStatus(name, 'completed');
      } else if (event.event === 'resume_gap') {
        // Replay buffer evicted the head of the turn (#398). Non-blocking:
        // keep the stream going and tell the user some events were skipped.
        useToastStore.getState().addToast(
          '对话回放被截断，部分中间事件未能重放。',
          'warning',
        );
      } else if (event.event === 'explorer_progress') {
        // #518: 归一化逻辑抽到 applyExplorerProgressToStore（与独立
        // /explorer/stream/{task_id} 消费者共用），聊天流与独立流一致。
        applyExplorerProgressToStore(data as Record<string, unknown>);
      }
    },
    [setSessionId, sessionIdRef, sessionTokenRef, rememberSessionToken, dispatchAction, markToolCallStatus, finalizeToolCalls, startExplorerProgressStream, onSessionPlanEvent]
  );

  // DUP-1: bounded auto-reconnect for the chat stream. Opt-in by explicit
  // config; the backend treats a re-POST carrying Last-Event-ID as a read-only
  // resume (replays missed events, never re-executes the turn), and replayed
  // events are deduped by id in useMapBridge. 2 attempts, 500ms→1s backoff.
  const bridge = useMapBridge(sessionId, dispatchAction, onEvent, sessionTokenRef, RECONNECT_OPTS, getSessionToken);
  const isLoading = bridge.aiStatus === 'thinking' || bridge.aiStatus === 'acting';

  const handlePlanAction = useCallback((planId: string, action: 'approve' | 'revise' | 'reject') => {
    const nextStatus: PlanProposalStatus =
      action === 'approve' ? 'approved' : action === 'revise' ? 'revising' : 'rejected';
    setMessages((prev) =>
      prev.map((m) =>
        m.plan?.plan_id === planId
          ? {
              ...m,
              plan: { ...m.plan, status: nextStatus },
            }
          : m
      )
    );
    const text =
      action === 'approve'
        ? `执行计划 ${planId}`
        : action === 'revise'
        ? `修改计划 ${planId}（说说哪里需要调整）`
        : `取消计划 ${planId}`;
    setTimeout(() => {
      // #468: the optimistic status above is only honest once the follow-up
      // send actually went through. A failed send (network death, exhausted
      // stream) left the card locked forever with the plan unexecuted — roll
      // it back to pending so the buttons are actionable again (the stream
      // error itself is surfaced separately by the bridge/chat error UI).
      const sent = handleSendRef.current?.(text);
      if (!sent || typeof sent.then !== 'function') return;
      const revert = () => {
        setMessages((prev) =>
          prev.map((m) =>
            m.plan?.plan_id === planId
              ? { ...m, plan: { ...m.plan, status: 'pending' } }
              : m
          )
        );
      };
      sent.then((ok) => {
        if (!ok) revert();
      }).catch(revert);
    }, 0);
  }, []);

  // #468: the ref carries handleSend's success signal (true = the turn
  // completed; false/rejection = failed) so plan approval can roll back.
  const handleSendRef = useRef<((text: string) => Promise<boolean>) | null>(null);
  const isLoadingRef = useRef(isLoading);
  isLoadingRef.current = isLoading;
  // F-5: synchronous in-flight guard. ``isLoadingRef`` is only refreshed during
  // render, so two sends in the same tick (before re-render) both passed the
  // guard and produced duplicate user/thinking messages plus a phantom "完成。"
  // bubble. This ref is set synchronously at entry and cleared in finally.
  const sendingRef = useRef(false);

  const handleSend = useCallback(
    async (userMsg: string): Promise<boolean> => {
      if (!userMsg || isLoadingRef.current || sendingRef.current) return false;

      // 「地图随对话」：新对话轮次。本轮 agent 展示图层时，旧轮的可见
      // 分析图层让位（lib/chat/turn-focus）。
      nextTurn();

      // #466: pending tool-arg evidence is TURN-scoped. Args queued by an
      // interrupted previous turn (stream cut, task_cancelled without
      // step_cancelled, exhausted reconnects) must never be FIFO-consumed by
      // THIS turn's step_results as wrong input evidence.
      useHudStore.getState().resetPendingToolArgs();

      const { viewport, baseLayer, is3D, layers: hudLayers, selectedFeature, focusLayerId } = useHudStore.getState();
      const liveSnapshot = getMapSnapshot();
      const mapState = {
        viewport: {
          center: liveSnapshot?.center ?? viewport.center,
          zoom: liveSnapshot?.zoom ?? viewport.zoom,
          bearing: liveSnapshot?.bearing ?? viewport.bearing ?? 0,
          pitch: liveSnapshot?.pitch ?? viewport.pitch ?? 0,
          bounds: liveSnapshot?.bounds ?? viewport.bounds ?? undefined,
        },
        base_layer: baseLayer,
        is_3d: is3D,
        layers: hudLayers.map((l: any) => ({
          id: l.id,
          name: l.name,
          type: l.type,
          visible: l.visible,
          opacity: l.opacity,
          group: l.group,
          _refId: l._refId,
          _tileUrl: l._tileUrl,
          _descriptor: l._descriptor,
          featureCount:
            l.source && typeof l.source === 'object' && 'features' in l.source
              ? (l.source as any).features?.length ?? 0
              : undefined,
          style: l.style,
          // Structured legend metadata is bounded and lets the backend compare
          // desired MapSpec semantics with the actual runtime observation.
          legend_spec: l.legend_spec,
        })),
        user_location: userLocation
          ? { lng: userLocation.lng, lat: userLocation.lat, accuracy: userLocation.accuracy }
          : null,
        // FE-4 (design §7)：选中要素快照必须是有界的 —— 父图层 id（非 __ 子图层）、
        // 稳定要素标识、≤5 个标量关键属性、可算时的 bbox。原始要素 payload /
        // geometry 永远不进 prompt 路径（后端 build_map_state_summary 只消费这些字段）。
        selected_feature: selectedFeature
          ? buildSelectedFeatureSnapshot(selectedFeature, hudLayers.map((l) => l.id))
          : null,
        // FE-4 (design §7)：用户聚焦图层（tool-call 卡片 / 图层面板聚焦）随 map_state
        // 上报，后端以"用户聚焦图层: Z"注入环境感知；无聚焦时省略。
        focus_layer_id: focusLayerId ?? null,
      };

      setMessages((prev) =>
        capMessages([
          ...prev,
          { id: msgIdGen.current.next(), role: 'user' as const, content: userMsg, timestamp: new Date() },
        ]),
      );

      const thinkingMsgId = msgIdGen.current.next();
      thinkingMsgIdRef.current = thinkingMsgId;
      tokenBatcherRef.current?.reset();
      thinkParserRef.current?.reset();
      setMessages((prev) =>
        capMessages([
          ...prev,
          {
            id: thinkingMsgId,
            role: 'assistant' as const,
            content: '',
            timestamp: new Date(),
            isThinking: true,
          },
        ]),
      );

      try {
        // F-5: set inside the try so a synchronous throw in the setup above
        // (which runs before this point, no awaits) cannot leave the guard
        // stuck. It is still set before the first await, so a same-tick second
        // send (which can only run once we yield at bridge.send) sees it.
        sendingRef.current = true;
        await bridge.send(userMsg, mapState);

        // Flush any tokens still pending in the current frame so the final
        // streamed text is applied before the thinking→done transition.
        tokenBatcherRef.current?.flush();

        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkingMsgId && (m as any).isThinking
              ? { ...m, isThinking: false, content: (m as any).content || '完成。' }
              : m
          )
        );
        // #468: turn outcome for optimistic-UI callers (plan approval). The
        // bridge resolves even when the stream died — the store's terminal
        // aiStatus (synced by useMapBridge before the send promise settles)
        // is the truthful signal.
        return useHudStore.getState().aiStatus !== 'error';
      } finally {
        // F-5: release the synchronous in-flight guard only after the send has
        // committed (success or error); by then isLoading governs re-entry.
        sendingRef.current = false;
      }
    },
    [bridge, getMapSnapshot, userLocation]
  );

  useEffect(() => {
    handleSendRef.current = handleSend;
  }, [handleSend]);

  return {
    messages,
    setMessages,
    aiStatus: bridge.aiStatus,
    isLoading,
    handleSend,
    handlePlanAction,
    bridge,
    agentRuntime,
  };
}
