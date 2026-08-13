'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useMapBridge } from './useMapBridge';
import { useHudStore } from '@/lib/store/useHudStore';
import { apiFetch, isApiError } from '@/lib/api/transport';
import { API_BASE } from '@/lib/api/config';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { SSEEvent } from '@/lib/api/chat';
import type { ToolCallEntry, PlanProposalPayload, SelectedFeatureInfo } from '@/lib/store/hud-types';
import type { AgentPlanState } from '@/lib/types/agent-plan';
import type { MapActionPayload } from '@/lib/types';
import { createMessageIdGenerator } from './use-message-id';
import { TokenBatcher } from './token-batcher';
import { IncrementalThinkParser, parseThink } from './incremental-think';


import { devOnly } from "@/lib/utils/logger";

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
  };
}

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

  const thinkingMsgIdRef = useRef<string>('');
  const msgIdGen = useRef(createMessageIdGenerator());
  const layerFetchAbortRef = useRef<AbortController | null>(null);

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
      } else if (event.event === 'tool_call') {
        // Result Workbench: stash the tool-call args so the matching step_result
        // can show truthful input evidence + parameters (best-effort, keyed by
        // tool name within the turn).
        if (data.name && typeof data.arguments === 'string') {
          useHudStore.getState().captureToolCallArgs(data.name, data.arguments);
        }
      } else if (event.event === "step_result") {
        // Result Workbench: normalize + record this result into the bounded,
        // session-scoped registry. Runs before the layer/chart handling so the
        // result is inspectable even when no layer is mounted. propose_plan and
        // other non-analysis events are ignored inside the slice. The returned
        // id lets the chat layer-added chip deep-link to the same result.
        const workbenchResultId = useHudStore.getState().captureStepResult(data);
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
          
          // V3 Performance: Extract descriptor from SSE payload (attached by execution_engine.py)
          const descriptor = data.ref_descriptor;
          
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
            _descriptor: descriptor,
            legend_spec: legendSpec,
          });
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
                label: 'Layer data error',
              }
            )
              .then((geojson) => {
                if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                  // Guard: only write if the layer still exists with this ref (not removed and re-added with different data)
                  const current = useHudStore.getState().layers.find((l) => l.id === fetchRef);
                  if (current) {
                    useHudStore.getState().updateLayer(fetchRef, { source: geojson });
                  }
                }
              })
              .catch((err) => {
                if (!isApiError(err)) {
                  devOnly.error('[LiveLayerFetch] Failed to fetch geojson_ref:', err);
                }
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
      } else if (event.event === 'step_cancelled') {
        // B-P2: 步骤被抢占取消时后端下发 step_cancelled
        // ({task_id, step_id, tool, session_id})。把对应 running 的 tool-call
        // 行标记为已取消（复用 ToolCallEntry 的 failed 形态），否则该行会一直
        // 停在 running 直到流结束。已到终态的行绝不覆盖；未匹配时原样返回 prev，
        // 保持 message 对象身份不变。
        const stepId = data.step_id;
        const tool = data.tool;
        if (typeof stepId === 'string' && stepId) {
          setMessages((prev) => {
            const msgIdx = prev.findIndex(
              (m) => m.id === thinkingId && Array.isArray(m.toolCalls),
            );
            if (msgIdx === -1) return prev;
            const toolCalls = prev[msgIdx].toolCalls;
            if (!toolCalls || toolCalls.length === 0) return prev;
            let changed = false;
            const nextCalls = toolCalls.map((c) => {
              const matches =
                c.id === stepId &&
                (tool === undefined || c.tool === tool) &&
                c.status === 'running';
              if (!matches) return c;
              changed = true;
              return { ...c, status: 'failed' as const, error: '已取消' };
            });
            if (!changed) return prev;
            const next = [...prev];
            next[msgIdx] = { ...prev[msgIdx], toolCalls: nextCalls };
            return next;
          });
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

  // DUP-1: bounded auto-reconnect for the chat stream. Opt-in by explicit
  // config; the backend treats a re-POST carrying Last-Event-ID as a read-only
  // resume (replays missed events, never re-executes the turn), and replayed
  // events are deduped by id in useMapBridge. 2 attempts, 500ms→1s backoff.
  const bridge = useMapBridge(sessionId, dispatchAction, onEvent, sessionTokenRef, {
    maxAttempts: 2,
    baseDelayMs: 500,
  });
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

      setMessages((prev) => [
        ...prev,
        { id: msgIdGen.current.next(), role: 'user' as const, content: userMsg, timestamp: new Date() },
      ]);

      const thinkingMsgId = msgIdGen.current.next();
      thinkingMsgIdRef.current = thinkingMsgId;
      tokenBatcherRef.current?.reset();
      thinkParserRef.current?.reset();
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
