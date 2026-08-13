'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { apiFetch, isApiError } from '@/lib/api/transport';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { ChatSession } from '@/lib/types/chat';
import type { MapActionPayload } from '@/lib/types';


import { devOnly } from "@/lib/utils/logger";
import {
  coalesceViewportState,
  resetViewportSeq,
  viewportSeqTracker,
} from "@/lib/utils/viewport-seq";
export function useWorkspaceSession(dispatchAction: (action: MapActionPayload) => void) {
  const [sessionId, setSessionId] = useState<string>();
  const sessionIdRef = useRef<string | undefined>(undefined);
  // SEC-08：匿名会话的 owner_token。服务端在新建匿名会话时签发，前端持有并在
  // 后续请求的 X-Session-Token 头里回传。认证会话 / 旧匿名会话该 ref 为 null。
  const sessionTokenRef = useRef<string | null>(null);
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
  const clearResults = useHudStore((s) => s.clearResults);

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
  const refreshSessions = useCallback(async () => {
    try {
      const data = await apiFetch<{ sessions?: ChatSession[] }>('/api/v1/chat/sessions', {
        label: 'Session list error',
      });
      if (data.sessions) setSessions(data.sessions);
    } catch (err) {
      if (!isApiError(err)) devOnly.error('Fetch sessions failed:', err);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const selectSession = useCallback(
    async (sid: string, onRestoreMessages: (messages: any[]) => void) => {
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
      const token = sessionTokenRef.current;
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

        if (data.messages && data.messages.length > 0) {
          const restored = data.messages.map((m: any) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            timestamp: new Date(m.timestamp),
          }));
          restored.push({
            id: `session-switch-${Date.now()}`,
            role: 'assistant' as const,
            content: `已恢复历史会话「${data.title || '未命名'}」——共 ${data.messages.length} 条记录。可继续提问。`,
            timestamp: new Date(),
          });
          onRestoreMessages(restored);
        }

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
          for (const layer of state.layers || []) {
            if (layer._refId && layer._refId.startsWith('ref:')) {
              // SEC-08：匿名会话的图层引用数据同样受 owner_token 保护。
              apiFetch<GeoJSONFeatureCollection>(
                `/api/v1/layers/data/${encodeURIComponent(layer._refId)}?session_id=${encodeURIComponent(sid)}`,
                { signal, ownerToken: token, label: 'Layer data error' }
              )
                .then((geojson) => {
                  if (signal.aborted) return;
                  if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                    store.addLayer({ ...layer, source: geojson });
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
        if (err?.name !== 'AbortError') {
          devOnly.error('Load session failed:', err);
        }
      }
    },
    [clearLayers, clearAnnotations, clearTask, clearResults, setSelectedFeature, setAiStatus, dispatchAction]
  );

  const startNewSession = useCallback(
    (onClearMessages: () => void) => {
      setSessionId(undefined);
      // SEC-08：新会话清掉旧 token；新匿名会话创建后由 SSE 响应重新填充。
      sessionTokenRef.current = null;
      // 审计 F20：同 selectSession，新会话必须重置跨会话状态。
      clearLayers();
      clearAnnotations();
      clearOpsLog();
      clearCausalChain();
      setSelectedFeature(null);
      setAiStatus('idle');
      clearTask();
      // F4: per-session viewport seq tracker — fresh session, fresh counter.
      resetViewportSeq();
      // FE-15：移除死代码 localStorage.removeItem('webgis_session_id')
      // (session ID 从未写入 localStorage，此 removeItem 是 no-op)
      onClearMessages();
    },
    [clearLayers, clearAnnotations, clearOpsLog, clearCausalChain, setSelectedFeature, setAiStatus, clearTask]
  );

  return {
    sessionId,
    setSessionId,
    sessionIdRef,
    // SEC-08：暴露 token ref + 设置器。useSSEStream 在收到新会话的 owner_token
    // 时写入；其它调用方只读（getSessionToken）。
    sessionTokenRef,
    getSessionToken: () => sessionTokenRef.current,
    sessions,
    setSessions,
    refreshSessions,
    selectSession,
    startNewSession,
  };
}
