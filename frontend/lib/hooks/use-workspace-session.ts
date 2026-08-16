'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { apiFetch, isApiError } from '@/lib/api/transport';
import { API_BASE } from '@/lib/api/config';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { ChatSession } from '@/lib/types/chat';
import type { MapActionPayload } from '@/lib/types';


import { devOnly } from "@/lib/utils/logger";
import {
  coalesceViewportState,
  resetViewportSeq,
  viewportSeqTracker,
} from "@/lib/utils/viewport-seq";

const MAX_SESSION_OWNER_TOKENS = 128;

/**
 * #392: 把会话恢复失败转成对用户可见的提示文案。优先 FastAPI detail，
 * 网络层 TypeError 给固定文案；"不静默"——失败必须在 UI 上有交代。
 */
function sessionLoadErrorNotice(err: unknown): string {
  let detail: string;
  if (isApiError(err)) {
    const body = err.body as { detail?: unknown } | null;
    detail =
      body && typeof body === 'object' && typeof body.detail === 'string' && body.detail
        ? body.detail
        : `HTTP ${err.status}`;
  } else if (err instanceof TypeError) {
    detail = '网络错误，无法连接服务器';
  } else if (err instanceof Error && err.message) {
    detail = err.message;
  } else {
    detail = '未知错误';
  }
  return `加载会话失败：${detail}。历史记录未恢复，可开始新对话。`;
}

export function useWorkspaceSession(dispatchAction: (action: MapActionPayload) => void) {
  const [sessionId, setSessionId] = useState<string>();
  const sessionIdRef = useRef<string | undefined>(undefined);
  // SEC-08：匿名会话的 owner_token。服务端在新建匿名会话时签发，前端持有并在
  // 后续请求的 X-Session-Token 头里回传。认证会话 / 旧匿名会话该 ref 为 null。
  const sessionTokenRef = useRef<string | null>(null);
  // FE-P3-7: reactive mirror — page.tsx passed sessionTokenRef.current as a
  // prop, which lags one render whenever the token is (re)issued without a
  // sessionId change. Components needing the token re-render when it flips.
  const [activeToken, setActiveToken] = useState<string | null>(null);
  // Anonymous owner tokens are session capabilities, not workspace-global
  // credentials. Retain them by session so switching A → B cannot send A's
  // token with B's cartographic observation or map-action ACK.
  const sessionTokensRef = useRef<Map<string, string>>(new Map());
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const sessionLoadAbortRef = useRef<AbortController | null>(null);

  // FE-07：用单字段 selector 订阅，避免订阅整个 store。
  // 这里订阅的都是 actions（Zustand 中为稳定引用），选择它们本身零开销。
  const clearLayers = useHudStore((s) => s.clearLayers);
  const clearOpsLog = useHudStore((s) => s.clearOpsLog);
  const clearCausalChain = useHudStore((s) => s.clearCausalChain);
  const clearAnnotations = useHudStore((s) => s.clearAnnotations);
  const setStoreSessions = useHudStore((s) => s.setSessions);
  const setSelectedFeature = useHudStore((s) => s.setSelectedFeature);
  const setAiStatus = useHudStore((s) => s.setAiStatus);
  const clearTask = useHudStore((s) => s.clearTask);
  const clearExplorerTasks = useHudStore((s) => s.clearExplorerTasks);
  const clearResults = useHudStore((s) => s.clearResults);
  // #392: History 抽屉开关信号 —— 打开时触发会话列表刷新（见下方 effect）。
  const historyOpen = useHudStore((s) => s.historyOpen);

  // Sync sessionId state to ref
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // Sync sessions list to store for HistoryDrawer
  useEffect(() => {
    setStoreSessions(
      sessions.map((s) => ({
        id: s.id,
        title: s.title || '未命名',
        time: new Date(s.createdAt).toLocaleString('zh-CN') || '',
        msgs: s.messages?.length || 0,
        tags: [],
      }))
    );
  }, [sessions, setStoreSessions]);

  // Fetch session list on mount (Fast Path: deduped + cached)
  const sessionsFetchAbortRef = useRef<AbortController | null>(null);

  const refreshSessions = useCallback(async () => {
    sessionsFetchAbortRef.current?.abort();
    const ctrl = new AbortController();
    sessionsFetchAbortRef.current = ctrl;
    try {
      const data = await apiFetch<{ sessions?: ChatSession[] }>('/api/v1/chat/sessions', {
        label: 'Session list error',
        signal: ctrl.signal,
      });
      if (ctrl.signal.aborted) return; // a newer fetch superseded this one
      if (data.sessions) setSessions(data.sessions);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      if (!isApiError(err)) devOnly.error('Fetch sessions failed:', err);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
    // FE-P2-1: unmount abort — restore work previously kept mutating the
    // GLOBAL store (layers/results/messages) after the page was left; the
    // only abort sites were re-entry and startNewSession, never unmount.
    return () => {
      sessionsFetchAbortRef.current?.abort();
      sessionLoadAbortRef.current?.abort();
    };
  }, [refreshSessions]);

  // #392: 历史列表之前只在 mount 拉一次 —— 页面存活期内新建的会话
  // 永远不出现在 History 抽屉、顶栏一直显示"新会话"直到整页刷新。
  // History 抽屉打开（store 的 historyOpen 翻转为 true）时重新拉取。
  useEffect(() => {
    if (historyOpen) refreshSessions();
  }, [historyOpen, refreshSessions]);

  const selectSession = useCallback(
    async (sid: string, onRestoreMessages: (messages: any[], notice?: string) => void) => {
      // Cancel previous session restoration requests to avoid stale layer insertions
      sessionLoadAbortRef.current?.abort();
      const ctrl = new AbortController();
      sessionLoadAbortRef.current = ctrl;
      const signal = ctrl.signal;

      // 审计 F20：切换会话必须清空跨会话残留状态，否则 session B 第一条消息
      // 会把 session A 的 selectedFeature 当作 map_state 发给 AI（产生 hallucinated
      // 推理），旧 task 卡片/annotation 也会残留在新会话 UI 上。
      clearLayers();
      clearAnnotations();
      setSelectedFeature(null);
      setAiStatus('idle');
      clearTask();
      // #548: explorer task cards are session-scoped — a session switch must not
      // leak the previous session's cards into the new session's task tab.
      clearExplorerTasks();
      // Result Workbench: results reference session-scoped ref: cursors that are
      // dead in the new session — clear the registry to avoid stale inspection.
      clearResults();
      // F4: the viewport seq tracker is per-session (server seqs are
      // session-scoped) — reset before the restore GET so the coalesce below
      // compares against a fresh counter, not the previous session's.
      resetViewportSeq();
      // 审计 F38：之前 setSessionId(sid) 在 fetch 完成后才调，期间 sessionIdRef
      // 仍是旧值 -> 若用户在窗口内点 send，消息会发到旧 session。改为同步先 set。
      setSessionId(sid);
      sessionIdRef.current = sid;
      // SEC-08：切回某个会话时，前端通常仍持有该会话的 token（同一浏览器会话内）。
      // 旧会话 / 认证会话 token 为 null，头不发送，后端按 grandfather/认证放行。
      const token = sessionTokensRef.current.get(sid) ?? null;
      sessionTokenRef.current = token;
      setActiveToken(token);
      // #392: 消息恢复是否已成功写入。仅当"消息 GET 本身"失败时才用
      // 错误提示重置 transcript —— 若消息已恢复、只是后续 map-state /
      // 图层恢复失败，不能把刚恢复的 transcript 再清掉。
      let messagesRestored = false;
      try {
        // F-09：会话恢复必须校验 HTTP 状态。旧实现 res.json() 不检查 res.ok，
        // 404/500 的错误体被当作成功消费（JSON detail → 静默无消息；HTML 错误
        // 页 → SyntaxError 被外层 catch 吞掉）。改用统一 transport：非 2xx 抛
        // 类型化 ApiError（携带 FastAPI detail），并短路后续的 map-state / 图层
        // / 分析资产恢复——失败会话的状态不会写入 UI。
        const data = await apiFetch<{ messages?: any[]; title?: string }>(
          `/api/v1/chat/sessions/${sid}`,
          { signal, ownerToken: token }
        );
        if (signal.aborted) return;

        // #392: 无条件恢复 —— 之前只在 messages 非空时调 onRestoreMessages，
        // 空会话 / GET 失败时上一会话的完整聊天留在屏幕上而 sessionIdRef 已
        // 指向新会话，下一条消息会在新会话身份下延续旧 transcript。
        const restored = (data.messages ?? []).map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: new Date(m.timestamp),
        }));
        if (restored.length > 0) {
          restored.push({
            id: `session-switch-${Date.now()}`,
            role: 'assistant' as const,
            content: `已恢复历史会话「${data.title || '未命名'}」——共 ${data.messages?.length ?? 0} 条记录。可继续提问。`,
            timestamp: new Date(),
          });
        }
        onRestoreMessages(restored);
        messagesRestored = true;

        // setSessionId 已在函数开头同步调用（审计 F38），这里不再重复

        const stateData = await apiFetch<{ map_state?: any }>(
          `/api/v1/chat/sessions/${sid}/map-state`,
          { signal, ownerToken: token, label: 'Map state error' }
        );
        if (signal.aborted) return;
        const state = stateData?.map_state;
        if (state) {
          const store = useHudStore.getState();
          if (state.viewport) {
            // F4: ignore stale/older-seq viewport state — an in-flight
            // throttled POST the client already sent outranks the persisted
            // value, so a restore must never fly the map back to an older view.
            const coalesced = coalesceViewportState(
              viewportSeqTracker,
              state._viewport_seq,
              state.viewport
            );
            if (coalesced.viewport) {
              dispatchAction({
                command: 'fly_to',
                params: {
                  center: coalesced.viewport.center,
                  zoom: coalesced.viewport.zoom,
                  bearing: coalesced.viewport.bearing,
                  pitch: coalesced.viewport.pitch,
                },
              });
            }
          }
          if (state.base_layer) store.setBaseLayer(state.base_layer);
          const observation = state._cartographic_observation;
          const observationIsCurrent = (
            typeof state._current_cartographic_fingerprint === 'string'
            && typeof observation?.mapspec_fingerprint === 'string'
            && observation.mapspec_fingerprint
              === state._current_cartographic_fingerprint
          );
          const observedLayers = observationIsCurrent && Array.isArray(observation?.layers)
            ? observation.layers
            : [];
          const runtimeLayers = observedLayers.map((observed: any) => {
            const refId = observed._refId;
            const runtimeId = observed.runtime_store_id ?? refId ?? observed.id;
            const rasterSource = (
              typeof observed.raster_image === 'string'
              && Array.isArray(observed.raster_bbox)
              && observed.raster_bbox.length === 4
            ) ? {
                image: observed.raster_image,
                bbox: observed.raster_bbox,
              } : null;
            return {
              id: runtimeId,
              name: observed.name ?? `分析结果: ${observed.id}`,
              type: rasterSource
                ? 'heatmap'
                : ['vector', 'raster', 'tile', 'heatmap'].includes(observed.type)
                  ? observed.type
                  : 'vector',
              visible: observed.visible !== false,
              opacity: typeof observed.opacity === 'number' ? observed.opacity : 1,
              group: observed.group ?? 'analysis',
              source: rasterSource ?? ({
                type: 'FeatureCollection',
                features: [],
                metadata: { ref_id: refId },
              } as GeoJSONFeatureCollection),
              style: observed.style,
              legend_spec: observed.legend_spec,
              _refId: refId,
              _descriptor: observed._descriptor,
              _tileUrl: refId
                ? `${API_BASE}/api/v1/layers/data/${refId}/tiles/{z}/{x}/{y}.mvt?session_id=${sid}`
                : undefined,
              _mapspecFingerprint: observation.mapspec_fingerprint,
              _mapspecLayerId: observed.id,
              _mapspecProjectionFingerprint: observed.projection_fingerprint,
              _mapspecRepairActionId: observed.repair_action_id,
              _intentGeneration: typeof observed.intent_generation === 'number'
                ? observed.intent_generation
                : undefined,
            };
          });
          // The live post-reconcile observation is the final-map snapshot. It
          // outranks the turn-start `layers` state, which may predate the GIS
          // result. Legacy sessions without runtime evidence keep the old path.
          const layersToRestore = runtimeLayers.length > 0
            ? runtimeLayers
            : (state.layers || []);
          for (const layer of layersToRestore) {
            store.addLayer(layer);
            if (
              layer._refId
              && layer._refId.startsWith('ref:')
              && !(
                layer._descriptor?.mvt_capable
                && layer._descriptor?.feature_count > 5000
              )
              && !(layer.source && typeof layer.source === 'object' && 'image' in layer.source)
            ) {
              // SEC-08：匿名会话的图层引用数据同样受 owner_token 保护。
              apiFetch<GeoJSONFeatureCollection>(
                `/api/v1/layers/data/${encodeURIComponent(layer._refId)}?session_id=${encodeURIComponent(sid)}`,
                { signal, ownerToken: token, label: 'Layer data error' }
              )
                .then((geojson) => {
                  if (signal.aborted) return;
                  if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                    const current = useHudStore.getState().layers.find(
                      (candidate) => candidate.id === layer.id
                    );
                    if (current?._refId === layer._refId) {
                      useHudStore.getState().updateLayer(layer.id, { source: geojson });
                    }
                  }
                })
                .catch((err) => {
                  if (!isApiError(err)) devOnly.error('[LayerFetch]', err);
                });
            }
          }
        }

        // 审计 F39：切换会话后必须刷新分析资产列表，否则 session A 的资产
        // 残留在 session B 的 AnalysisTab 里。store 内部的 fetchAnalysisAssets
        // 走统一 listUploads（Fast Path + 集中错误/超时处理）。
        try {
          await useHudStore.getState().fetchAnalysisAssets(sid);
        } catch (e) {
          if (!isApiError(e)) devOnly.warn('[fetchAnalysisAssets] failed on session switch:', e);
        }
      } catch (err: any) {
        // Log the failure (typed or not) so debugging surface is uniform;
        // AbortError stays silent because it's the expected outcome of
        // session-switch cancellation.
        if (err?.name === 'AbortError') return;
        devOnly.error('Load session failed:', err);
        // #392: 消息 GET 失败 -> 无条件重置 transcript（否则上一会话的
        // 聊天残留屏幕、下一条消息延续旧 transcript），并附错误提示，
        // 不再静默吞掉失败。
        if (!messagesRestored) {
          onRestoreMessages([], sessionLoadErrorNotice(err));
        }
      }
    },
    [clearLayers, clearAnnotations, clearTask, clearExplorerTasks, clearResults, setSelectedFeature, setAiStatus, dispatchAction]
  );

  const startNewSession = useCallback(
    (onClearMessages: () => void) => {
      // F-1: abort any in-flight session restore. selectSession aborts its own
      // controller on re-entry, but startNewSession did not — so a slow restore
      // for session A could resolve AFTER the user started a new session and
      // mutate the fresh session (onRestoreMessages / addLayer / fetchAnalysisAssets).
      sessionLoadAbortRef.current?.abort();
      sessionLoadAbortRef.current = null;
      setSessionId(undefined);
      // FE-P3-1: selectSession syncs the ref synchronously (F38); this path
      // relied on the post-render effect — a programmatic send in the same
      // tick (plan approve/reject defers handleSend by one macrotask) read
      // the OLD session id and posted the message + map snapshot there.
      sessionIdRef.current = undefined;
      // SEC-08：新会话清掉旧 token；新匿名会话创建后由 SSE 响应重新填充。
      sessionTokenRef.current = null;
      setActiveToken(null);
      // 审计 F20：同 selectSession，新会话必须重置跨会话状态。
      clearLayers();
      clearAnnotations();
      clearOpsLog();
      clearCausalChain();
      setSelectedFeature(null);
      setAiStatus('idle');
      clearTask();
      // #548: new session = fresh explorer task tab (same session-scope rule as
      // selectSession, this path had no clear at all before).
      clearExplorerTasks();
      // F-4: the result registry references session-scoped ``ref:`` cursors that
      // are dead in the new session — clear it (selectSession does; this path
      // did not), so a stale Result Workbench does not persist into the new
      // session. Also resets pendingArgs (F-3 leak surface).
      clearResults();
      // F4: per-session viewport seq tracker — fresh session, fresh counter.
      resetViewportSeq();
      // FE-15：移除死代码 localStorage.removeItem('webgis_session_id')
      // (session ID 从未写入 localStorage，此 removeItem 是 no-op)
      onClearMessages();
    },
    [clearLayers, clearAnnotations, clearOpsLog, clearCausalChain, setSelectedFeature, setAiStatus, clearTask, clearExplorerTasks, clearResults]
  );

  const rememberSessionToken = useCallback((sid: string, token: string) => {
    if (!sid || !token) return;
    // Cap capability retention: long-lived tabs may visit many anonymous
    // sessions, but ACK routing only needs a bounded recent working set.
    if (!sessionTokensRef.current.has(sid)) {
      while (sessionTokensRef.current.size >= MAX_SESSION_OWNER_TOKENS) {
        const oldest = sessionTokensRef.current.keys().next().value;
        if (!oldest) break;
        sessionTokensRef.current.delete(oldest);
      }
    }
    sessionTokensRef.current.set(sid, token);
    if (!sessionIdRef.current || sessionIdRef.current === sid) {
      sessionTokenRef.current = token;
      setActiveToken(token);
    }
  }, []);

  const getSessionTokenFor = useCallback((sid: string): string | null => {
    return sessionTokensRef.current.get(sid) ?? (
      sessionIdRef.current === sid ? sessionTokenRef.current : null
    );
  }, []);

  return {
    sessionId,
    setSessionId,
    sessionIdRef,
    // SEC-08：暴露 token ref + 设置器。useSSEStream 在收到新会话的 owner_token
    // 时写入；其它调用方只读（getSessionToken）。
    sessionTokenRef,
    /** FE-P3-7: render-fresh mirror of the active session's owner token. */
    activeSessionToken: activeToken,
    rememberSessionToken,
    getSessionTokenFor,
    getSessionToken: () => sessionTokenRef.current,
    sessions,
    setSessions,
    refreshSessions,
    selectSession,
    startNewSession,
  };
}
