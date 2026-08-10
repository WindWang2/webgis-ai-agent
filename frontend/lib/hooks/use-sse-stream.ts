'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useMapBridge } from './useMapBridge';
import { useHudStore } from '@/lib/store/useHudStore';
import { API_BASE } from '@/lib/api/config';
import type { SSEEvent } from '@/lib/api/chat';
import type { ToolCallEntry, PlanProposalPayload } from '@/lib/store/hud-types';
import type { AgentPlanState } from '@/lib/types/agent-plan';
import type { MapActionPayload } from '@/lib/types';
import { createMessageIdGenerator } from './use-message-id';
import { TokenBatcher } from './token-batcher';


import { devOnly } from "@/lib/utils/logger";
export function useSSEStream(
  sessionId: string | undefined,
  setSessionId: (sid: string) => void,
  sessionIdRef: React.MutableRefObject<string | undefined>,
  dispatchAction: (act: MapActionPayload) => void,
  getMapSnapshot: () => any,
  userLocation: { lng: number; lat: number; accuracy?: number } | null,
  sessionTokenRef: React.MutableRefObject<string | null>
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

  const thinkingMsgIdRef = useRef<string>('');
  const msgIdGen = useRef(createMessageIdGenerator());
  const layerFetchAbortRef = useRef<AbortController | null>(null);

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
      const parsed = parseThink(snapshot.content);
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

  const parseThink = useCallback((raw: string) => {
    const start = raw.indexOf('<think>');
    const end = raw.indexOf('</think>');
    if (start !== -1 && end !== -1 && end > start) {
      return {
        thinking: raw.slice(start + 7, end),
        content: raw.slice(0, start) + raw.slice(end + 8).trimStart(),
      };
    }
    if (start !== -1) {
      return { thinking: raw.slice(start + 7), content: raw.slice(0, start) };
    }
    return { thinking: '', content: raw };
  }, []);

  const onEvent = useCallback(
    (event: SSEEvent) => {
      const data = event.data as any;

      // Session ID assignment (first response carries the server-assigned session)
      if (data?.session_id && data.session_id !== sessionIdRef.current) {
        setSessionId(data.session_id);
        sessionIdRef.current = data.session_id;
      }

      // SEC-08：服务端在新建匿名会话时签发 owner_token（随 task_start / session 事件下发）。
      // 前端持有后在后续请求的 X-Session-Token 头里回传。认证会话不携带该字段。
      if (data?.owner_token && typeof data.owner_token === 'string') {
        sessionTokenRef.current = data.owner_token;
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
      } else if (event.event === "step_result") {
        // Plan Mode：propose_plan 返回的 plan 摘要挂到当前消息，由 PlanProposalCard 渲染
        if (data.tool === 'propose_plan' && data.result?.success && data.result?.plan_id) {
          const plan: PlanProposalPayload = {
            plan_id: data.result.plan_id,
            title: data.result.title,
            summary: data.result.summary,
            step_count: data.result.step_count,
            destructive_steps: data.result.destructive_steps || [],
            steps_preview: data.result.steps_preview || [],
            status: 'pending',
          };
          setMessages((prev) => prev.map((m) => (m.id === thinkingId ? { ...m, plan } : m)));
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
          const layerMetaTitle: string | null = data.result?.layer_meta?.title ?? null;
          // Detect native heatmap
          const isNativeHeatmap =
            data.tool === 'heatmap_data' &&
            (data.result?.command === 'add_native_heatmap' ||
              data.result?.metadata?.render_type === 'native');
          useHudStore.getState().addLayer({
            id: layerId,
            name: layerName,
            type: data.result?.image ? 'heatmap' : isNativeHeatmap ? 'heatmap' : 'vector',
            visible: !data.geojson_ref, // image-only layers have no ref_id so display_layer can't show them
            opacity: 1,
            group: 'analysis',
            source: data.geojson_ref
              ? ({
                  type: 'FeatureCollection',
                  features: [],
                  metadata: { ref_id: data.geojson_ref },
                } as any)
              : data.result,
            style: { color: accentColor },
            _refId: data.geojson_ref,
            // Data Plane: 大要素 ref 图层由 MVT 瓦片端点显示（替代整包 GeoJSON）。
            _tileUrl: data.geojson_ref
              ? `${API_BASE}/api/v1/layers/data/${data.geojson_ref}/tiles/{z}/{x}/{y}.mvt?session_id=${sessionIdRef.current}`
              : undefined,
            legend_spec: legendSpec,
          });
          if (layerMetaTitle) {
            useHudStore.getState().setCartographyTitle(layerMetaTitle);
          }

          // Asynchronously fetch the actual GeoJSON data for the reference
          if (data.geojson_ref) {
            const sid = sessionIdRef.current;
            const fetchRef = data.geojson_ref;
            // SEC-08：匿名会话的图层引用数据受 owner_token 保护。
            const token = sessionTokenRef.current;
            fetch(`${API_BASE}/api/v1/layers/data/${fetchRef}?session_id=${sid}`, {
              signal: layerFetchAbortRef.current?.signal,
              headers: token ? { 'X-Session-Token': token } : {},
            })
              .then((r) => (r.ok ? r.json() : null))
              .then((geojson) => {
                if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                  // Guard: only write if the layer still exists with this ref (not removed and re-added with different data)
                  const current = useHudStore.getState().layers.find((l) => l.id === fetchRef);
                  if (current) useHudStore.getState().updateLayer(fetchRef, { source: geojson });
                }
              })
              .catch((err) =>
                devOnly.error('[LiveLayerFetch] Failed to fetch geojson_ref:', err)
              );
          }

          setMessages((prev) =>
            prev.map((m) => (m.id === thinkingId ? { ...m, layerAdded: layerName } : m))
          );
        }
        // Chart data from generate_chart tool — attach to message for rendering in chat
        if (data.result?.chart) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === thinkingId
                ? { ...m, charts: [...((m.charts as any[]) ?? []), data.result.chart] }
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
                        status: 'pending' as const,
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
        event.event === 'error' ||
        event.event === 'step_error' ||
        event.event === 'task_error'
      ) {
        // B-P2-13: previously any error/step_error/task_error replaced the
        // ENTIRE message with a generic string, discarding whatever had
        // already streamed and the server's real error detail. Preserve the
        // partial answer and append the actual error (or a fallback note).
        const raw = data?.error;
        const detail =
          typeof raw === "string" && raw.trim()
            ? raw
            : event.event === "step_error"
              ? "工具执行失败。"
              : "请求失败，请重试。";
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
      } else if (event.event === 'explorer_progress') {
        const taskId = data.task_id as string;
        const stage = data.stage as import('@/lib/types/explorer').ExplorerStage;
        const status = data.status as string;
        const context = (data.context as Record<string, unknown>) || {};
        useHudStore.getState().updateExplorerTask(taskId, {
          stage,
          status:
            status === 'completed'
              ? 'completed'
              : status === 'failed'
              ? 'failed'
              : status === 'decision_point'
              ? 'decision_required'
              : (`${stage}ing` as any),
          progress: (context?.progress as number) || 0,
        });
      }
    },
    [setSessionId, sessionIdRef, sessionTokenRef]
  );

  const bridge = useMapBridge(sessionId, dispatchAction, onEvent, sessionTokenRef);
  const isLoading = bridge.aiStatus === 'thinking' || bridge.aiStatus === 'acting';

  const handlePlanAction = useCallback((planId: string, action: 'approve' | 'revise' | 'reject') => {
    setMessages((prev) =>
      prev.map((m) =>
        m.plan?.plan_id === planId
          ? {
              ...m,
              plan: { ...m.plan, status: action === 'approve' ? 'approved' : 'rejected' },
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
    setTimeout(() => handleSendRef.current?.(text), 0);
  }, []);

  const handleSendRef = useRef<((text: string) => void) | null>(null);
  const isLoadingRef = useRef(isLoading);
  isLoadingRef.current = isLoading;

  const handleSend = useCallback(
    async (userMsg: string) => {
      if (!userMsg || isLoadingRef.current) return;

      const { viewport, baseLayer, is3D, layers: hudLayers, selectedFeature } = useHudStore.getState();
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
          featureCount:
            l.source && typeof l.source === 'object' && 'features' in l.source
              ? (l.source as any).features?.length ?? 0
              : undefined,
          style: l.style,
        })),
        user_location: userLocation
          ? { lng: userLocation.lng, lat: userLocation.lat, accuracy: userLocation.accuracy }
          : null,
        selected_feature: selectedFeature
          ? {
              layer_id: selectedFeature.layerId,
              layer_name: selectedFeature.layerName ?? null,
              ref_id: selectedFeature.refId ?? null,
              point: selectedFeature.point,
              properties: selectedFeature.properties,
              selected_at: selectedFeature.selectedAt,
            }
          : null,
      };

      setMessages((prev) => [
        ...prev,
        { id: msgIdGen.current.next(), role: 'user' as const, content: userMsg, timestamp: new Date() },
      ]);

      const thinkingMsgId = msgIdGen.current.next();
      thinkingMsgIdRef.current = thinkingMsgId;
      tokenBatcherRef.current?.reset();
      setMessages((prev) => [
        ...prev,
        {
          id: thinkingMsgId,
          role: 'assistant' as const,
          content: '',
          timestamp: new Date(),
          isThinking: true,
        },
      ]);

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
  };
}
