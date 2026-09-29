/**
 * Chat SSE 事件 typed adapters（H03 / #1554）。
 *
 * use-sse-stream 的 onEvent if/else 链吸收了大量领域逻辑（step_result
 * 巨分支 ~230 行、map_finalization 披露、plan 载荷规范化、错误分类）。
 * 本模块把各事件域的处理拆为**纯决策函数**：`(payload, ctx) => Decision`
 * —— 不触 store/window/fetch；hook 作为薄 orchestration 外壳执行决策
 * （store 写、fetch 派发、dispatchAction）。决策函数可独立做决策表测试。
 *
 * 与 message-reducer.ts 的分工：reducer 只做消息数组变换；adapter 做事件
 * 载荷 → 领域动作的翻译（含 HUD/工作台/地图 mutation 决策）。
 */
import type { StepResultPayload } from '@/lib/api/chat';
import type { StepResultEvent } from '@/lib/results/types';
import type { ToolCallEntry, PlanProposalPayload } from '@/lib/store/hud-types';
import type { AgentPlanState } from '@/lib/types/agent-plan';
import type { MapActionPayload } from '@/lib/types';
import type { ChatStreamMessage, ToolCallStatus } from '@/lib/chat/message-reducer';
import { buildMvtTileUrl } from '@/lib/map-kit/tile-url';

// ── token adapter ──────────────────────────────────────────────────────

export interface TokenDecision {
  chunk: string;
  isReasoning: boolean;
}

/** token/content → TokenBatcher 入参；非 token 类事件返回 null。 */
export function tokenDecision(event: { event: string; data: unknown }): TokenDecision | null {
  if (event.event !== 'token' && event.event !== 'content') return null;
  const data = event.data as { content?: string; is_reasoning?: boolean; type?: string };
  return {
    chunk: data?.content || '',
    isReasoning: !!(data?.is_reasoning || data?.type === 'reasoning'),
  };
}

// ── plan events adapter（plan_ready / plan_step_done / plan_finalized）──

/** plan_ready 载荷 → AgentPlanState（#615 恢复计划的 done → status）。 */
export function planReadyDecision(
  data: Record<string, unknown>,
): AgentPlanState | null {
  try {
    const steps = (data.steps ?? []) as Array<Record<string, unknown>>;
    return {
      intent: data.intent as string,
      domains: (data.domains as string[]) ?? [],
      steps: steps.map((s) => ({
        n: s.n as number,
        goal: s.goal as string,
        // 后端缺席时回退 "core"（与 legacy plan_ready 发射同源语义）
        tool_family: (s.tool_family as string) ?? 'core',
        // #615: restored plans carry done:bool per step — map to the
        // status PlanCard renders, don't hardcode pending.
        status: s.done ? ('done' as const) : ('pending' as const),
      })),
      finalized: false,
    };
  } catch {
    return null;
  }
}

// ── map_finalization adapter（ADR-0081 完成态披露）────────────────────

export interface MapFinalizationContext {
  /** 当前会话 id（INV-2 跨会话守卫）。 */
  sessionId: string | undefined;
  /** 同会话同状态去重状态（review H-6；hook 持有，adapter 保持纯）。 */
  lastNotice: { sessionId: string; status: string; notice: string } | null;
}

export interface MapFinalizationDecision {
  /** 携带 bbox 才有可执行的视口动作（review D-6 幂等门）。 */
  command: MapActionPayload | null;
  /** 需要用户可见 toast 的异常披露（完成态零噪声）。 */
  notice: { text: string } | null;
  /** 去重状态的新值（调用方回写；null 表示不变）。 */
  nextLastNotice: MapFinalizationContext['lastNotice'];
}

export function mapFinalizationDecision(
  data: unknown,
  ctx: MapFinalizationContext,
  /** finalizationUserNotice 注入（避免 adapters→finalizer 反向耦合测试）。 */
  noticeOf: (payload: unknown) => string | null,
): MapFinalizationDecision {
  const payload = (data ?? {}) as {
    status?: string;
    result_bbox?: number[];
    session_id?: string;
  };
  // INV-2 同款跨会话守卫：旧会话迟到的 finalization 不得把新会话的相机 fit 走。
  if (
    typeof payload.session_id === 'string'
    && payload.session_id
    && payload.session_id !== ctx.sessionId
  ) {
    return { command: null, notice: null, nextLastNotice: null };
  }
  let command: MapActionPayload | null = null;
  if (Array.isArray(payload.result_bbox)) {
    command = {
      command: 'MAP_FINALIZATION',
      params: {
        status: String(payload.status ?? 'pending'),
        bbox: payload.result_bbox as [number, number, number, number],
      },
    };
  }
  const noticeText = noticeOf(payload);
  let notice: { text: string } | null = null;
  let nextLastNotice = ctx.lastNotice;
  if (noticeText) {
    const currentStatus = String(payload.status ?? '');
    const dupes = ctx.lastNotice
      && ctx.lastNotice.sessionId === (ctx.sessionId ?? '')
      && ctx.lastNotice.status === currentStatus
      && ctx.lastNotice.notice === noticeText;
    if (!dupes) {
      notice = { text: noticeText };
      nextLastNotice = { sessionId: ctx.sessionId ?? '', status: currentStatus, notice: noticeText };
    }
  }
  return { command, notice, nextLastNotice };
}

// ── error events adapter（error / step_error / task_error）─────────────

export interface ErrorEventDecision {
  /** step_error：终态对应工具行（V7 两段式匹配键）。 */
  toolStatus: { tool: string; error?: string; stepId?: string } | null;
  /** 流级死亡：清空 turn-scoped pending args（#466）。 */
  resetPendingToolArgs: boolean;
  /** 流级死亡：兜底终结全部 running 行（#608）。 */
  finalizeAllRunning: boolean;
  /** 用户可见 detail（B-P2-13：保留已流式内容，只追加 detail）。 */
  detail: string;
}

export function errorEventDecision(event: { event: string; data: unknown }): ErrorEventDecision {
  const data = (event.data ?? {}) as { error?: unknown; tool?: unknown; step_id?: unknown };
  const isStep = event.event === 'step_error';
  const isStreamLevel = event.event === 'error' || event.event === 'task_error';
  const raw = data?.error;
  const detail =
    typeof raw === 'string' && raw.trim()
      ? raw
      : isStep
        ? '工具执行失败。'
        : '请求失败，请重试。';
  return {
    toolStatus: isStep && typeof data?.tool === 'string' && data.tool
      ? {
          tool: data.tool,
          error: typeof raw === 'string' ? raw : undefined,
          stepId: typeof data?.step_id === 'string' ? data.step_id : undefined,
        }
      : null,
    resetPendingToolArgs: isStreamLevel,
    finalizeAllRunning: isStreamLevel,
    detail,
  };
}

// ── step_result adapter（最大巨分支）────────────────────────────────────

export interface StepResultAdapterContext {
  sessionId: string | undefined;
  accentColor: string;
  /** 时间注入（adapter 保持可测确定性；图层 id 兜底与 mapspec 代戳）。 */
  now: number;
  /** i18n 注入（图层语义名：搜索结果/热力分析/分析结果）。 */
  labelFor: (tool: string, name?: string) => string;
}

export interface LayerMountDecision {
  layerId: string;
  /** addLayer 的完整入参（字段与旧 wire 计算逐一同源）。 */
  addLayerArg: Record<string, unknown>;
  /** runtimePatch + ref 存在时的镜像更新（幂等，见 hook 注释）。 */
  updateLayerArg: { id: string; arg: Record<string, unknown> } | null;
  /** agent 声明 visible → 标记当前轮并收起旧轮可见层（turn-focus）。 */
  agentDisplayed: boolean;
  /** layer_meta.title → HUD cartography 标题。 */
  cartographyTitle: string | null;
  /** V3 Performance：GeoJSON vs MVT 拉取决策（fetch 派发仍归 hook）。 */
  shouldFetchFullFC: boolean;
}

export interface StepResultDecision {
  /** Result Workbench 记录（captureStepResult 入参）。 */
  workbenchCapture: StepResultEvent | null;
  /** ToolCallChain 终态（completed）。 */
  toolStatus: { tool: string; stepId?: string; extra?: Partial<ToolCallEntry> } | null;
  /** propose_plan 结果 → 消息 plan 挂载。 */
  planProposal: PlanProposalPayload | null;
  /** deep_explore explorer_task → 独立进度流（#518）。 */
  explorerTaskId: string | null;
  /** 图层挂载决策。 */
  layerMount: LayerMountDecision | null;
  /** chart 数据（generate_chart）。 */
  chart: unknown;
  /** 挂载 chip + workbench 深链（消息 reducer 入参）。 */
  chip: { layerName: string; resultId: string | undefined } | null;
}

// 大要素 ref 图层内联 GeoJSON 的阈值（V3 Performance，与 hook 旧值一致）。
export const VECTOR_TILE_THRESHOLD = 5000;

export function stepResultDecision(
  data: StepResultPayload,
  ctx: StepResultAdapterContext,
): StepResultDecision {
  const result = data.result;
  // Result Workbench：capture 在图层处理前，保证无图层挂载也可检视。
  const workbenchCapture = data as unknown as StepResultEvent;

  // FE-P3-3: 终态迁移（V7 step_id 优先；#608 hasGeojson 附加）。
  const tool = String(data.tool ?? '');
  const stepId = typeof data.step_id === 'string' ? data.step_id : undefined;
  const toolStatus = tool
    ? {
        tool,
        stepId,
        extra: {
          ...(data.geojson_ref ? { hasGeojson: true, layerId: String(data.geojson_ref) } : {}),
          result: data.result,
        } as Partial<ToolCallEntry>,
      }
    : null;

  // Plan Mode：propose_plan 摘要。
  let planProposal: PlanProposalPayload | null = null;
  if (tool === 'propose_plan' && result?.success && result?.plan_id) {
    const planResult = result as {
      plan_id: string; title: string; summary?: string;
      step_count?: number; destructive_steps?: string[];
      steps_preview?: PlanProposalPayload['steps_preview'];
    };
    planProposal = {
      plan_id: planResult.plan_id,
      title: planResult.title,
      summary: planResult.summary,
      step_count: planResult.step_count ?? 0,
      destructive_steps: planResult.destructive_steps ?? [],
      steps_preview: planResult.steps_preview ?? [],
      status: 'pending',
    };
  }

  // #518: deep_explore 后台任务。
  const explorerTaskId =
    result?.type === 'explorer_task' && typeof result?.task_id === 'string' && result.task_id
      ? result.task_id
      : null;

  // 图层挂载（hidden by default；AI 调 display_layer 展示最终结果）。
  let layerMount: LayerMountDecision | null = null;
  let chip: StepResultDecision['chip'] = null;
  if (data.geojson_ref || result?.image) {
    const layerId = data.geojson_ref ?? `layer-${ctx.now}`;
    const layerName = ctx.labelFor(tool, typeof data.name === 'string' ? data.name : undefined);
    const accentColor = ctx.accentColor;
    const legendSpec = result?.legend_spec ?? undefined;
    const runtimePatch = result?.runtime_patch;
    const patchVisible = typeof runtimePatch?.visible === 'boolean' ? runtimePatch.visible : true;
    const patchOpacity =
      typeof runtimePatch?.opacity === 'number' && Number.isFinite(runtimePatch.opacity)
        ? runtimePatch.opacity
        : 1;
    const patchLegend = runtimePatch?.legend_spec ?? legendSpec;
    const patchStyle =
      runtimePatch?.style && typeof runtimePatch.style === 'object'
        ? runtimePatch.style
        : { color: accentColor };
    const layerMetaTitle: string | null = result?.layer_meta?.title ?? null;
    const isNativeHeatmap =
      tool === 'heatmap_data'
      && (result?.command === 'add_native_heatmap' || result?.metadata?.render_type === 'native');
    const descriptor = data.ref_descriptor;

    const addLayerArg = {
      id: layerId,
      name: layerName,
      type: result?.image ? 'heatmap' : isNativeHeatmap ? 'heatmap' : 'vector',
      visible: patchVisible,
      opacity: patchOpacity,
      group: 'analysis',
      source: data.geojson_ref
        ? { type: 'FeatureCollection', features: [], metadata: { ref_id: data.geojson_ref } }
        : result,
      style: patchStyle,
      _refId: data.geojson_ref ?? runtimePatch?.image_ref,
      _tileUrl: data.geojson_ref
        ? buildMvtTileUrl(data.geojson_ref, ctx.sessionId, descriptor?.content_revision)
        : undefined,
      _descriptor: descriptor,
      legend_spec: patchLegend,
      _mapspecFingerprint: runtimePatch?.mapspec_fingerprint,
      _mapspecLayerId: runtimePatch?.layer_id,
      _mapspecGenerationAt: runtimePatch ? ctx.now : undefined,
      _mapspecProjectionFingerprint: runtimePatch?.projection_fingerprint,
      _cartographicRepairs: Array.isArray(runtimePatch?.repair_attempts)
        ? runtimePatch!.repair_attempts.slice(0, 2)
        : undefined,
    };
    // 已存在的 ref 图层走镜像更新而不是重复 add（命名不得回退）。
    const updateLayerArg = runtimePatch && data.geojson_ref
      ? {
          id: layerId,
          arg: {
            name: layerMetaTitle || layerName,
            visible: patchVisible,
            opacity: patchOpacity,
            style: patchStyle,
            legend_spec: patchLegend,
            _refId: data.geojson_ref,
            _descriptor: descriptor,
            _mapspecFingerprint: runtimePatch.mapspec_fingerprint,
            _mapspecLayerId: runtimePatch.layer_id,
            _mapspecGenerationAt: ctx.now,
            _mapspecProjectionFingerprint: runtimePatch.projection_fingerprint,
            _cartographicRepairs: Array.isArray(runtimePatch.repair_attempts)
              ? runtimePatch.repair_attempts.slice(0, 2)
              : undefined,
          },
        }
      : null;

    const shouldFetchFullFC =
      !descriptor
      || !descriptor.mvt_capable
      || descriptor.feature_count <= VECTOR_TILE_THRESHOLD;

    layerMount = {
      layerId,
      addLayerArg: addLayerArg as Record<string, unknown>,
      updateLayerArg: updateLayerArg as { id: string; arg: Record<string, unknown> } | null,
      agentDisplayed: patchVisible,
      cartographyTitle: layerMetaTitle,
      shouldFetchFullFC,
    };
    chip = { layerName, resultId: undefined /* hook 注入 workbench id */ };
  }

  const chart = result?.chart ?? null;

  return { workbenchCapture, toolStatus, planProposal, explorerTaskId, layerMount, chart, chip };
}

// ── 消息副作用汇总（hook 执行面用）────────────────────────────────────

export type MessageOp =
  | { kind: 'appendToolCall'; toolCall: { tool: string; arguments?: string; stepId?: string; startedAt: number } }
  | { kind: 'markToolCallsStatus'; tool: string; status: ToolCallStatus; error?: string; stepId?: string; extra?: Partial<ToolCallEntry> }
  | { kind: 'attachPlanProposal'; plan: PlanProposalPayload }
  | { kind: 'attachLayerChip'; layerName: string; resultId?: string }
  | { kind: 'attachChart'; chart: unknown };

/** ChatStreamMessage 的图层 chip 应用（供 hook 侧类型引用）。 */
export type ChatMessageOf = ChatStreamMessage;
