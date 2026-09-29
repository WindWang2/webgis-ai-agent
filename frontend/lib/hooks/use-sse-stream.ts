'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import type { StepResultPayload } from '@/lib/api/chat';
import { useMapBridge } from './useMapBridge';
import { useHudStore } from '@/lib/store/useHudStore';
import { useChatStore, type ChatMessage } from '@/lib/store/useChatStore';
import { markRefSourceFailed } from '@/lib/mapspec/ref-source-resolver';
import { requestRefFC } from '@/lib/data-plane/ref-service';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { SSEEvent } from '@/lib/api/chat';
import type { SelectedFeatureInfo } from '@/lib/store/hud-types';
import { reportLayerFetchFailure, syncSpecLayersToStore } from '@/lib/session/map-state-restore';
import { commitMapSpecDocument, setMapSpecRevision, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { nextTurn, noteAgentDisplayed } from '@/lib/chat/turn-focus';
import { useToastStore } from '@/components/ui/toast';
import type { MapActionPayload } from '@/lib/types';
import { createMessageIdGenerator } from './use-message-id';
import { TokenBatcher } from './token-batcher';
import { IncrementalThinkParser, parseThink } from './incremental-think';
import { streamExplorerProgress } from '@/lib/api/explorer';
import { getAccessToken, getRefreshToken } from '@/lib/auth/tokenStore';
import type { ExplorerStage, ExplorerStatus } from '@/lib/types/explorer';
import { t } from '@/lib/i18n/t';


import { devOnly } from "@/lib/utils/logger";
import { finalizationUserNotice } from "@/lib/map-product/finalizer";
import { parseAgentRuntime, type AgentRuntime } from "@/lib/agent-runtime";
import { configureAffordanceChannel, extractMountedWidget } from "@/lib/copilot/affordance";

// H03 / #1554：消息数组变换的纯 reducer 与事件域 typed adapters。
// 本 hook 只保留 transport 编排（TokenBatcher 生命周期、fetch 派发、
// store 写入、守卫顺序），消息变换与事件决策全部下沉。
import {
  appendMessage,
  appendToolCall,
  applyTokenSnapshot,
  attachChart,
  attachLayerChip,
  attachPlanProposal,
  attachAgentPlan,
  clearThinkingFlag,
  finalizeAgentPlan,
  finalizeRunningToolCalls,
  markAgentPlanStepDone,
  markToolCallsStatus,
  appendTurnNotice,
  settleThinkingMessage,
  setPlanStatus,
  type ChatStreamMessage,
} from '@/lib/chat/message-reducer';
import {
  errorEventDecision,
  mapFinalizationDecision,
  planReadyDecision,
  stepResultDecision,
  tokenDecision,
} from '@/lib/chat/adapters';

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
 * FE-P3-2: 消息总量上界已随 reducer 下沉（lib/chat/message-reducer.ts，
 * MAX_CHAT_MESSAGES=200）。此处仅保留欢迎语 seeding。
 */
const WELCOME_MESSAGE: ChatStreamMessage = {
  id: '1',
  role: 'assistant',
  // #1436 lineage: 文案在 i18n message catalog（chat.welcomeExtended, zh+en）。
  // content 在挂载时点经 t() 解析（见下方 seeding effect）—— 命令式 t()
  // 不能在此模块级调用，否则 locale 冻结在 import 时刻。
  content: '',
  timestamp: null,
};

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

// ADR-0081 披露去噪（review H-6）：同一会话内**同状态**的相同 finalization
// 通知只 toast 一次 —— 卡在 needs_repair 的会话每个触发点都会重发披露；
// 状态变化（needs_repair → failed 等）仍会再次提醒（终审 F2：纯文本匹配
// 会永久吞掉回归信号）。
// H03：去重状态改为 hook 持有的 ref 注入 mapFinalizationDecision（adapter
// 保持纯决策；原模块级单例在多 hook 实例（测试）间串扰）。
interface FinalizationNotice {
  sessionId: string;
  status: string;
  notice: string;
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
        t('chat.deepExploreTask', { id: taskId.slice(0, 8) }),
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

/** #1554：options 对象签名（10 位置参数 → 具名字段）。 */
export interface UseSSEStreamOptions {
  sessionId: string | undefined;
  setSessionId: (sid: string) => void;
  sessionIdRef: React.MutableRefObject<string | undefined>;
  dispatchAction: (act: MapActionPayload) => void;
  getMapSnapshot: () => any;
  userLocation: { lng: number; lat: number; accuracy?: number } | null;
  sessionTokenRef: React.MutableRefObject<string | null>;
  rememberSessionToken?: (sessionId: string, token: string) => void;
  getSessionToken?: (sessionId: string) => string | null;
  /**
   * #1048: session_plan_* 增量的出口。三个事件名在本 hook 的既有分发链中
   * 识别（plan_* 分支保持不动），载荷原样转交；信封关联与状态应用在
   * useSessionPlan（page.tsx 接线）。可选项：既有调用方与测试不受影响。
   * 必须传稳定引用（useSessionPlan 返回的 applySessionPlanEvent），
   * 否则 onEvent 身份抖动会打断在飞流。
   */
  onSessionPlanEvent?: (eventName: string, data: Record<string, unknown>) => void;
}

export function useSSEStream(options: UseSSEStreamOptions) {
  const {
    sessionId,
    setSessionId,
    sessionIdRef,
    dispatchAction,
    getMapSnapshot,
    userLocation,
    sessionTokenRef,
    rememberSessionToken,
    getSessionToken,
    onSessionPlanEvent,
  } = options;

  // H03 / #1554：useChatStore 是消息的**单一 owner**（此前 hook useState
  // 为 owner、streaming-chat-host 每次 render 后 useEffect 镜像写 store，
  // store 读者拿到滞后副本，page.tsx 还会双写）。本 hook 直接读写 store。
  const messages = useChatStore((s) => s.messages) as ChatStreamMessage[];
  const setMessages = useCallback(
    (updater: ChatStreamMessage[] | ((prev: ChatStreamMessage[]) => ChatStreamMessage[])) => {
      useChatStore.getState().setMessages(
        updater as ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[]),
      );
    },
    [],
  );

  // 欢迎语 seeding：store 为空时补欢迎消息（保持旧 useState 初始化的 UI
  // 行为；幂等 —— StrictMode 双挂载/多实例不重复插）。
  useEffect(() => {
    if (useChatStore.getState().messages.length === 0) {
      // #1436：挂载时点读当前 locale（zh-CN 文案与旧硬编码逐字一致）。
      setMessages([{ ...WELCOME_MESSAGE, content: t('chat.welcomeExtended') }]);
    }
  }, [setMessages]);

  const [agentRuntime, setAgentRuntime] = useState<AgentRuntime | null>(null);

  const thinkingMsgIdRef = useRef<string>('');
  // FE-P3-3 / #608：终态迁移与兜底 —— 变换经纯 reducer，写入单一 store。
  const markToolCallStatus = useCallback(
    (tool: string, status: 'completed' | 'failed', error?: string, extra?: Partial<import('@/lib/store/hud-types').ToolCallEntry>, stepId?: string): void => {
      useChatStore.getState().setMessages((prev) =>
        markToolCallsStatus(prev as ChatStreamMessage[], {
          messageId: thinkingMsgIdRef.current,
          tool, status, error, stepId, extra, completedAt: Date.now(),
        }) as unknown as ChatMessage[],
      );
    },
    [],
  );
  const finalizeToolCalls = useCallback(
    (status: 'completed' | 'failed', error?: string): void => {
      useChatStore.getState().setMessages((prev) =>
        finalizeRunningToolCalls(prev as ChatStreamMessage[], {
          messageId: thinkingMsgIdRef.current,
          status, error, completedAt: Date.now(),
        }) as unknown as ChatMessage[],
      );
    },
    [],
  );
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
  // store write per animation frame instead of one per token. The batcher owns
  // the accumulated content/reasoning (snapshot semantics) and fires onFlush on
  // rAF; onFlush applies the snapshot via the pure reducer. Created once;
  // reset() is called per turn.
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
      useChatStore.getState().setMessages((prev) =>
        applyTokenSnapshot(prev as ChatStreamMessage[], {
          messageId: thinkingId,
          content: parsed.content,
          thinking: parsed.thinking || snapshot.reasoning,
        }) as unknown as ChatMessage[],
      );
    });
  }

  // Reset abort controller on session change to cancel in-flight layer fetches.
  // undefined → assigned is the server binding the currently live stream
  // (useMapBridge 同款豁免), not a session switch — aborting there kills the
  // fetches started before the bind.
  const prevSessionIdRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const prev = prevSessionIdRef.current;
    const sessionChanged = prev !== undefined && prev !== sessionId;
    prevSessionIdRef.current = sessionId;
    if (sessionChanged) {
      layerFetchAbortRef.current?.abort();
      explorerAbortRef.current?.abort();
      explorerStreamsRef.current.clear();
    }
    if (!layerFetchAbortRef.current || layerFetchAbortRef.current.signal.aborted) {
      layerFetchAbortRef.current = new AbortController();
    }
    if (!explorerAbortRef.current || explorerAbortRef.current.signal.aborted) {
      explorerAbortRef.current = new AbortController();
    }
  }, [sessionId]);

  // Unmount: abort both in-flight channels (explorer streams belong to the
  // session; layer fetches must not resolve into a torn-down workbench).
  useEffect(
    () => () => {
      layerFetchAbortRef.current?.abort();
      explorerAbortRef.current?.abort();
    },
    [],
  );

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
    // V7（审计 §6-M）：有限重连 —— 此前非 abort 失败只 warn，任务卡「进行中」
    // 直到终态（槽位只在 completed/failed 释放）。最多重试 2 次（指数退避
    // 1s/2s）；终态释放槽位的语义不变，重试不复活已终态的任务流。
    (async () => {
      const MAX_ATTEMPTS = 3;
      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
        try {
          let sawTerminal = false;
          for await (const ev of streamExplorerProgress(taskId, signal)) {
            if (ev.event === 'explorer_progress' && ev.data && typeof ev.data === 'object') {
              applyExplorerProgressToStore(ev.data as Record<string, unknown>);
              // 终态后释放 per-task 槽位：未来可重开流（断线恢复），且不再去重拦截。
              const status = (ev.data as Record<string, unknown>).status;
              if (status === 'completed' || status === 'failed') {
                sawTerminal = true;
                explorerStreamsRef.current.delete(taskId);
              }
            }
          }
          // 服务端正常收尾但未发终态（连接被对端关闭）：等同断线，走重试判定。
          if (sawTerminal) return;
        } catch (err) {
          if (err instanceof DOMException && err.name === 'AbortError') return;
          devOnly.warn(`[useSSEStream] explorer progress stream attempt ${attempt} failed:`, err);
        }
        if (signal?.aborted) return;
        // 重试前预留：槽位仍持有 → 同 taskId 不会并发重开。
        if (attempt < MAX_ATTEMPTS) await new Promise((r) => setTimeout(r, 1000 * attempt));
      }
      // 次数耗尽仍无终态：释放槽位并如实记日志（不再无限占位）。
      explorerStreamsRef.current.delete(taskId);
      devOnly.warn('[useSSEStream] explorer progress stream gave up after retries:', taskId);
    })();
  }, []);

  // ADR-0081 finalization 披露去重状态（adapter ctx 注入）。
  const lastFinalizationNoticeRef = useRef<FinalizationNotice | null>(null);

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

      const token = tokenDecision(event);
      if (!token) {
        // Apply any pending batched tokens before a non-token event (status
        // change, layer add, plan, error) so the final streamed text lands
        // first. No-op when nothing is pending.
        tokenBatcherRef.current?.flush();
      }
      if (token) {
        tokenBatcherRef.current?.push(token.chunk, token.isReasoning);
      } else if (event.event === 'tool_call') {
        // Result Workbench: stash the tool-call args so the matching step_result
        // can show truthful input evidence + parameters (best-effort, keyed by
        // tool name within the turn).
        if (data.name && typeof data.arguments === 'string') {
          useHudStore.getState().captureToolCallArgs(data.name, data.arguments);
        }
        // FE-P3-3: populate the thinking message's ToolCallChain（reducer
        // appendToolCall：同 stepId 幂等 —— 重放不产生双行）。
        const toolName = typeof data.name === 'string' ? data.name : '';
        if (toolName) {
          setMessages((prev) => appendToolCall(prev, {
            messageId: thinkingId,
            toolCall: {
              tool: toolName,
              arguments: typeof data.arguments === 'string' ? data.arguments : undefined,
              stepId: typeof data.step_id === 'string' ? data.step_id : undefined,
              startedAt: Date.now(),
            },
          }));
        }
      } else if (event.event === "step_result") {
        // #1009: 分支内收窄到 step_result 最小契约（字段漂移由 tsc 捕获）
        const payload = event.data as StepResultPayload;
        const decision = stepResultDecision(payload, {
          sessionId: sessionIdRef.current,
          accentColor: useHudStore.getState().accentColor,
          now: Date.now(),
          labelFor: (toolName, name) =>
            toolName === 'search_poi'
              ? t('chat.searchResult', { name: name || 'POI' })
              : toolName === 'heatmap_data'
                ? t('chat.heatmapAnalysis')
                : t('chat.analysisResult', { tool: toolName ?? 'unknown' }),
        });
        // Result Workbench: normalize + record（在图层处理前，保证无图层
        // 挂载也可检视）；返回 id 供 chip 深链。
        const workbenchResultId = useHudStore.getState().captureStepResult(
          decision.workbenchCapture as never,
        );
        if (decision.toolStatus) {
          markToolCallStatus(
            decision.toolStatus.tool,
            'completed',
            undefined,
            decision.toolStatus.extra,
            decision.toolStatus.stepId,
          );
        }
        if (decision.planProposal) {
          setMessages((prev) => attachPlanProposal(prev, {
            messageId: thinkingId,
            plan: decision.planProposal!,
          }));
        }
        if (decision.explorerTaskId) {
          startExplorerProgressStream(decision.explorerTaskId);
        }
        if (decision.layerMount) {
          const mount = decision.layerMount;
          useHudStore.getState().addLayer(mount.addLayerArg as never);
          // A GIS result is often auto-mounted hidden before the agent authors
          // its final MapSpec.  In that case addLayer is intentionally a no-op;
          // update the existing ref layer with the reviewed presentation and
          // generation instead of creating a duplicate layer（命名不得回退）。
          if (mount.updateLayerArg) {
            const { id, arg } = mount.updateLayerArg as { id: string; arg: Record<string, unknown> };
            useHudStore.getState().updateLayer(id, arg as never, { source: 'server' });
          }
          // 「地图随对话」：runtime_patch 声明 visible → 标记当前轮。
          if (mount.agentDisplayed) {
            noteAgentDisplayed(mount.layerId);
          }
          if (mount.cartographyTitle) {
            useHudStore.getState().setCartographyTitle(mount.cartographyTitle);
          }
          if (mount.shouldFetchFullFC && payload.geojson_ref) {
            const sid = sessionIdRef.current;
            const fetchRef = payload.geojson_ref;
            const layerName = (mount.addLayerArg as { name?: string }).name ?? fetchRef;
            // SEC-08：匿名会话的图层引用数据受 owner_token 保护。
            const token0 = sessionTokenRef.current;
            // extreme-scale v2：统一数据面调度器（单飞/预算/ETag/取消）。
            // 会话切换的 abort 仍走 layerFetchAbortRef 信号；取消后的迟到
            // 完成由调度器 stale 闸 + session 守卫双层防护。
            requestRefFC({
              sessionId: sid ?? '',
              refId: fetchRef,
              // F13：数据身份 revision 进缓存键（与 MVT tile URL 的
              // v=<content_revision> 同源；#1112 同 ref 覆盖不串数据）。
              dataRevision: payload.ref_descriptor?.content_revision,
              ownerToken: token0,
              urgency: 'interactive',
              reasonCode: 'sse:add-layer',
              signal: layerFetchAbortRef.current?.signal,
            })
              .then((res) => {
                if (res.status === 'cancelled') return; // 预期控制流（会话切换/卸载）
                if (res.status === 'failed' || !res.fc) {
                  reportLayerFetchFailure(
                    '[LiveLayerFetch] Failed to fetch geojson_ref:',
                    layerName,
                    res.error,
                  );
                  // Workspace V2：失败回执进 resolver 墓碑（TTL 有界）—— 否则
                  // HUD 挂载路径的死 ref 只留空占位 FC，状态词表会永远显示
                  // 「加载中」而不是「已过期」。
                  try {
                    markRefSourceFailed(fetchRef);
                  } catch {
                    /* 墓碑是增值投影，失败不阻断 */
                  }
                  return;
                }
                const geojson = res.fc;
                if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                  // #1385 F03：会话切换后不得把 A 的 FC 写入 B 并飞到 A 范围。
                  if (sessionIdRef.current !== sid) return;
                  // Guard: only write if the layer still exists with this ref (not removed and re-added with different data)
                  const current = useHudStore.getState().layers.find((l) => l.id === fetchRef);
                  if (current && current._refId === fetchRef) {
                    useHudStore.getState().updateLayer(fetchRef, { source: geojson as unknown as GeoJSONFeatureCollection });
                    // Store-mounted add_layer never reaches the handler flyTo.
                    // Frame the fetched features so POIs are not a 1px spec
                    // on the default China view.
                    useHudStore.getState().focusLayer(fetchRef);
                  }
                }
              });
          }
          setMessages((prev) => attachLayerChip(prev, {
            messageId: thinkingId,
            layerName: (mount.addLayerArg as { name?: string }).name ?? '',
            resultId: workbenchResultId,
          }));
        }
        if (decision.chart) {
          setMessages((prev) => attachChart(prev, {
            messageId: thinkingId,
            chart: decision.chart,
          }));
        }
      } else if (event.event === 'plan_ready') {
        const agentPlan = planReadyDecision(data);
        if (agentPlan) {
          setMessages((prev) => attachAgentPlan(prev, { messageId: thinkingId, agentPlan }));
        } else {
          devOnly.warn('[plan_ready] parse failed');
        }
      } else if (event.event === 'plan_step_done') {
        setMessages((prev) => markAgentPlanStepDone(prev, {
          messageId: thinkingId,
          stepN: data.step_n,
        }));
      } else if (event.event === 'plan_finalized') {
        setMessages((prev) => finalizeAgentPlan(prev, {
          messageId: thinkingId,
          skipped: data.skipped ?? [],
        }));
      } else if (
        event.event === 'session_plan_updated' ||
        event.event === 'session_plan_progress' ||
        event.event === 'session_plan_superseded' ||
        event.event === 'session_plan_step'
      ) {
        // #1048 + ADR-0180: SessionPlan 实时增量（Pi/legacy 双 host，与上方
        // plan_* 是两个计划概念，ADR-0076）。载荷是冻结的线上投影
        // （session_plan.py / harness_kernel 构造），本 hook 只在既有分发链
        // 里识别事件名并原样转交；信封关联与状态应用在 useSessionPlan。
        // 跨会话事件已被本函数顶部的 INV-2 守卫丢弃。
        onSessionPlanEvent?.(event.event, data as Record<string, unknown>);
      } else if (event.event === 'map_finalization') {
        // ADR-0081：后端 Completion Runtime 的完成态披露。决策经
        // mapFinalizationAdapter（跨会话守卫/bbox 幂等门/去重），本层只执行。
        const decision = mapFinalizationDecision(
          data,
          {
            sessionId: sessionIdRef.current,
            lastNotice: lastFinalizationNoticeRef.current,
          },
          (p) => finalizationUserNotice(p as unknown as Parameters<typeof finalizationUserNotice>[0]),
        );
        if (decision.command) {
          dispatchAction(decision.command);
        }
        if (decision.notice) {
          lastFinalizationNoticeRef.current = decision.nextLastNotice;
          try {
            useToastStore.getState().addToast(decision.notice.text, 'warning');
          } catch {
            devOnly.warn('[MapFinalization]', decision.notice.text);
          }
        }
      } else if (event.event === 'step_cancelled') {
        // B-P2: 步骤被抢占取消时后端下发 step_cancelled。把对应 running 的
        // tool-call 行标记为已取消（复用 failed 形态），否则该行会一直停在
        // running 直到流结束。R2F-2: 取消的调用不会发 step_result —— 丢弃
        // 其排队 args，重试的 step_result 与重试的 args 配对。
        const tool = data.tool;
        if (typeof tool === 'string' && tool) {
          useHudStore.getState().discardPendingToolArgs(tool);
          markToolCallStatus(tool, 'failed', '已取消');
        }
      } else if (event.event === 'task_cancelled') {
        // #466: 抢占取消的 task 不再发 step_result/step_cancelled —— 清空
        // turn-scoped pending args + 兜底终结全部 running 行。
        useHudStore.getState().resetPendingToolArgs();
        finalizeToolCalls('failed', '已取消');
        setMessages((prev) => appendTurnNotice(prev, {
          messageId: thinkingId,
          kind: 'cancelled',
        }));
      } else if (
        event.event === 'error' ||
        event.event === 'step_error' ||
        event.event === 'task_error'
      ) {
        // B-P2-13: 保留已流式内容，只追加真实错误 detail（R2F-2/#466/#608
        // 语义经 errorEventAdapter 决策）。
        const decision = errorEventDecision(event);
        if (decision.toolStatus) {
          useHudStore.getState().discardPendingToolArgs(decision.toolStatus.tool);
          markToolCallStatus(
            decision.toolStatus.tool,
            'failed',
            decision.toolStatus.error,
            undefined,
            decision.toolStatus.stepId,
          );
        } else if (decision.resetPendingToolArgs) {
          useHudStore.getState().resetPendingToolArgs();
        }
        if (decision.finalizeAllRunning) {
          finalizeToolCalls('failed', decision.detail);
        }
        setMessages((prev) => appendTurnNotice(prev, {
          messageId: thinkingId,
          kind: 'error',
          detail: decision.detail,
        }));
      } else if (event.event === 'done' || event.event === 'task_complete') {
        // #518: isThinking 必须在终态事件到达时翻转（连接仍可保持打开推送
        // explorer 进度）。#608: 残留 running 行兜底终结，spinner 不永转。
        finalizeToolCalls('failed', '未收到执行结果');
        if (thinkingId) {
          setMessages((prev) => clearThinkingFlag(prev, { messageId: thinkingId }));
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
      } else if (event.event === 'ui_action') {
        // ADR-0194：生成式微 UI 挂载（mount_widget）。extractMountedWidget
        // 是 mount 前最后一道校验（isWidgetSpecSafe），不合规静默丢弃。
        const widget = extractMountedWidget(data);
        if (widget) {
          useHudStore.getState().pushCopilotWidget(widget);
        }
      }
    },
    [setSessionId, sessionIdRef, sessionTokenRef, rememberSessionToken, dispatchAction, markToolCallStatus, finalizeToolCalls, setMessages, startExplorerProgressStream, onSessionPlanEvent]
  );

  // DUP-1: bounded auto-reconnect for the chat stream. Opt-in by explicit
  // config; the backend treats a re-POST carrying Last-Event-ID as a read-only
  // resume (replays missed events, never re-executes the turn), and replayed
  // events are deduped by id in useMapBridge. 2 attempts, 500ms→1s backoff.
  // ADR-0194：画布可供性上报通道注册（会话 id / 匿名 owner_token 由会话
  // 持有方提供；未注册时 reportAffordance 退化为仅 stage）。
  useEffect(() => {
    configureAffordanceChannel({
      getSessionId: () => sessionIdRef.current ?? null,
      getOwnerToken: () =>
        getSessionToken?.(sessionIdRef.current ?? '') ?? sessionTokenRef.current,
    });
  }, [sessionIdRef, sessionTokenRef, getSessionToken]);

  const bridge = useMapBridge(sessionId, dispatchAction, onEvent, sessionTokenRef, RECONNECT_OPTS, getSessionToken);
  const isLoading = bridge.aiStatus === 'thinking' || bridge.aiStatus === 'acting';

  const handlePlanAction = useCallback((planId: string, action: 'approve' | 'revise' | 'reject') => {
    const nextStatus = action === 'approve' ? 'approved' : action === 'revise' ? 'revising' : 'rejected';
    setMessages((prev) => setPlanStatus(prev, { planId, status: nextStatus }));
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
        setMessages((prev) => setPlanStatus(prev, { planId, status: 'pending' }));
      };
      sent.then((ok) => {
        if (!ok) revert();
      }).catch(revert);
    }, 0);
  }, [setMessages]);

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

      setMessages((prev) => appendMessage(prev, {
        id: msgIdGen.current.next(),
        role: 'user',
        content: userMsg,
        timestamp: new Date(),
      }));

      const thinkingMsgId = msgIdGen.current.next();
      thinkingMsgIdRef.current = thinkingMsgId;
      tokenBatcherRef.current?.reset();
      thinkParserRef.current?.reset();
      setMessages((prev) => appendMessage(prev, {
        id: thinkingMsgId,
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        isThinking: true,
      }));

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

        setMessages((prev) => settleThinkingMessage(prev, { messageId: thinkingMsgId }));
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
    [bridge, getMapSnapshot, userLocation, setMessages]
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
