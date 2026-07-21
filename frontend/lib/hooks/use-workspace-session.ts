'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { API_BASE } from '@/lib/api/config';
import type { ChatSession } from '@/lib/types/chat';
import type { MapActionPayload } from '@/lib/types';


import { devOnly } from "@/lib/utils/logger";
export function useWorkspaceSession(dispatchAction: (action: MapActionPayload) => void) {
  const [sessionId, setSessionId] = useState<string>();
  const sessionIdRef = useRef<string | undefined>(undefined);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const sessionLoadAbortRef = useRef<AbortController | null>(null);

  const {
    clearLayers,
    clearOpsLog,
    clearCausalChain,
    clearAnnotations,
    setSessions: setStoreSessions,
    setSelectedFeature,
    setAiStatus,
    clearTask,
  } = useHudStore();

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

  // Fetch session list on mount
  const refreshSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/chat/sessions`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.sessions) setSessions(data.sessions);
    } catch (err) {
      devOnly.error('Fetch sessions failed:', err);
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
      // 审计 F38：之前 setSessionId(sid) 在 fetch 完成后才调，期间 sessionIdRef
      // 仍是旧值 -> 若用户在窗口内点 send，消息会发到旧 session。改为同步先 set。
      setSessionId(sid);
      sessionIdRef.current = sid;
      try {
        const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}`, { signal });
        const data = await res.json();
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

        const stateRes = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}/map-state`, { signal });
        if (signal.aborted) return;
        if (stateRes.ok) {
          const stateData = await stateRes.json();
          const state = stateData?.map_state;
          if (state) {
            const store = useHudStore.getState();
            if (state.viewport) {
              dispatchAction({
                command: 'fly_to',
                params: {
                  center: state.viewport.center,
                  zoom: state.viewport.zoom,
                  bearing: state.viewport.bearing,
                  pitch: state.viewport.pitch,
                },
              });
            }
            if (state.base_layer) store.setBaseLayer(state.base_layer);
            for (const layer of state.layers || []) {
              if (layer._refId && layer._refId.startsWith('ref:')) {
                fetch(`${API_BASE}/api/v1/layers/data/${layer._refId}?session_id=${sid}`, { signal })
                  .then((r) => (r.ok ? r.json() : null))
                  .then((geojson) => {
                    if (signal.aborted) return;
                    if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                      store.addLayer({ ...layer, source: geojson });
                    }
                  })
                  .catch((err) => {
                    if (err?.name !== 'AbortError') devOnly.error('[LayerFetch]', err);
                  });
              }
            }
          }
        }

        // 审计 F39：切换会话后必须刷新分析资产列表，否则 session A 的资产
        // 残留在 session B 的 AnalysisTab 里。之前 fetchAnalysisAssets 从未被调用。
        try {
          await useHudStore.getState().fetchAnalysisAssets(sid);
        } catch (e) {
          devOnly.warn('[fetchAnalysisAssets] failed on session switch:', e);
        }
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          devOnly.error('Load session failed:', err);
        }
      }
    },
    [clearLayers, dispatchAction]
  );

  const startNewSession = useCallback(
    (onClearMessages: () => void) => {
      setSessionId(undefined);
      // 审计 F20：同 selectSession，新会话必须重置跨会话状态。
      clearLayers();
      clearAnnotations();
      clearOpsLog();
      clearCausalChain();
      setSelectedFeature(null);
      setAiStatus('idle');
      clearTask();
      localStorage.removeItem('webgis_session_id');
      onClearMessages();
    },
    [clearLayers, clearAnnotations, clearOpsLog, clearCausalChain, setSelectedFeature, setAiStatus, clearTask]
  );

  return {
    sessionId,
    setSessionId,
    sessionIdRef,
    sessions,
    setSessions,
    refreshSessions,
    selectSession,
    startNewSession,
  };
}
