/**
 * Interaction IR V1 —— 事件总线单例与类型化 emitter（ADR-0227）。
 *
 * 生产接线面（UI hook 只调用这里的 emit* 函数，一行一条事实）：
 * - 会话/token 来源与用户 mutation 同源（getMapSpecSessionCursor）——
 *   事件与 mutation 走同一身份与归属面；
 * - event_id/client_seq 在此铸造（uuid + per-tab 单调计数）；
 * - 无会话（SSR / 会话未建立）时 emit 静默丢弃 —— 观察层绝不反向驱动
 *   会话生命周期。
 *
 * 几何摘要 geometryDigest 是**非密码学**噪声容忍摘要（坐标四舍五入到
 * ~1e-6 度后 FNV 组合哈希）：用于去重/关联，不用于完整性校验。
 */
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  INTERACTION_SCHEMA_VERSION,
  type InteractionEventKind,
  type InteractionEventV1,
  type InteractionEventRefs,
} from './contract';
import {
  createInteractionOutbox,
  getClientInstanceId,
  nextClientSeq,
  registerPageHideFlush,
  type InteractionOutbox,
} from './outbox';

let outbox: InteractionOutbox | null = null;
let unregisterPageHide: (() => void) | null = null;

function ensureOutbox(): InteractionOutbox {
  if (outbox) return outbox;
  outbox = createInteractionOutbox({
    getSessionId: () => {
      try {
        return getMapSpecSessionCursor().sessionId ?? undefined;
      } catch {
        return undefined;
      }
    },
    getOwnerToken: (sessionId) => {
      try {
        const cursor = getMapSpecSessionCursor();
        if (sessionId && cursor.sessionId !== sessionId) return null;
        return cursor.ownerToken ?? null;
      } catch {
        return null;
      }
    },
  });
  unregisterPageHide = registerPageHideFlush(() => outbox?.flushKeepalive());
  return outbox;
}

/** 测试/进程退出：拆掉单例（含 pagehide 监听）。 */
export function disposeInteractionBus(): void {
  unregisterPageHide?.();
  unregisterPageHide = null;
  outbox?.dispose();
  outbox = null;
}

function mintEventId(): string {
  const c = typeof crypto !== 'undefined' ? crypto : undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `intir-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** 铸造并入队一条事件（无会话时静默丢弃）。 */
export function emitInteraction(
  kind: InteractionEventKind,
  payload: Record<string, unknown>,
  refs?: InteractionEventRefs,
): void {
  try {
    if (!getMapSpecSessionCursor().sessionId) return;
    const event: InteractionEventV1 = {
      schema_version: INTERACTION_SCHEMA_VERSION,
      event_id: mintEventId(),
      actor: 'user',
      client_instance_id: getClientInstanceId(),
      client_seq: nextClientSeq(),
      kind,
      payload,
      created_at: new Date().toISOString(),
      ...(refs ?? {}),
    };
    ensureOutbox().enqueue(event);
  } catch {
    // 观察层绝不阻塞交互主链路。
  }
}

/** 立即冲刷（会话切换前调用，最大化已发事实）。 */
export function flushInteractionBus(): void {
  outbox?.flush();
}

// ── 几何/字段摘要（隐私最小化：只留摘要，不留本体）──────────────────────

/** 坐标圆整到 ~1e-6 度（≈0.1m）—— 消除浮点噪声后做摘要。 */
function roundCoord(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

/** 非密码学 64bit 组合摘要（FNV-1a × 2 不同种子）→ 16 hex。 */
export function geometryDigest(
  geometry: { type?: string; coordinates?: unknown } | null | undefined,
): string | undefined {
  if (!geometry) return undefined;
  try {
    const canonical = JSON.stringify(geometry, (_key, value: unknown) => {
      if (typeof value === 'number' && Number.isFinite(value)) return roundCoord(value);
      return value;
    });
    let h1 = 0xcbf29ce4 >>> 0;
    let h2 = 0x84222325 >>> 0;
    for (let i = 0; i < canonical.length; i += 1) {
      const code = canonical.charCodeAt(i);
      h1 = Math.imul(h1 ^ code, 0x01000193) >>> 0;
      h2 = Math.imul(h2 ^ code, 0x01000193) >>> 0;
      h2 = (h2 ^ (h1 >>> 7)) >>> 0;
    }
    return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0'));
  } catch {
    return undefined;
  }
}

/** bbox → 摘要（同款噪声容忍）。 */
export function bboxDigest(
  bbox: [number, number, number, number] | null | undefined,
): string | undefined {
  if (!bbox || bbox.length !== 4) return undefined;
  return geometryDigest({
    type: 'bbox',
    coordinates: bbox.map((v) => roundCoord(v)),
  });
}

/** 属性键 allowlist：字段**名**（≤8 个，仅名字 —— 值一律不进 ledger）。 */
export function fieldNamesOf(
  properties: Record<string, unknown> | null | undefined,
): string[] | undefined {
  if (!properties) return undefined;
  const names = Object.keys(properties)
    .filter((key) => key !== 'id')
    .sort()
    .slice(0, 8);
  return names.length > 0 ? names : undefined;
}

/** 几何顶点数（防泄几何本体的规模披露）。 */
export function vertexCountOf(
  geometry: { type?: string; coordinates?: unknown } | null | undefined,
): number | undefined {
  const countCoordinates = (value: unknown, depth = 0): number => {
    if (depth > 6 || !Array.isArray(value)) return 0;
    if (typeof value[0] === 'number' && typeof value[1] === 'number') return 1;
    return value.reduce((sum: number, item) => sum + countCoordinates(item, depth + 1), 0);
  };
  if (!geometry?.coordinates) return undefined;
  const count = countCoordinates(geometry.coordinates);
  return count > 0 ? count : undefined;
}

// ── 类型化 emitter（生产接线面）────────────────────────────────────────

export function emitViewportSettled(view: {
  center: [number, number];
  zoom: number;
  bearing?: number;
  pitch?: number;
  bounds?: [number, number, number, number];
  viewportSeq?: number;
}): void {
  emitInteraction('viewport_settled', {
    center: [roundCoord(view.center[0]), roundCoord(view.center[1])],
    zoom: Math.round(view.zoom * 1000) / 1000,
    ...(view.bearing !== undefined ? { bearing: Math.round(view.bearing * 1000) / 1000 } : {}),
    ...(view.pitch !== undefined ? { pitch: Math.round(view.pitch * 1000) / 1000 } : {}),
    ...(view.bounds
      ? {
        bounds: view.bounds.map((v) => roundCoord(v)),
      }
      : {}),
  }, view.viewportSeq !== undefined ? { viewport_seq: view.viewportSeq } : undefined);
}

export function emitFeatureSelected(info: {
  layerId: string;
  featureId?: string | number;
  properties?: Record<string, unknown>;
  bbox?: [number, number, number] | null;
  datasetRef?: string;
  /** 多选集（/goal 03 additive）：与 featureId 同源解析的选中 id 集；
   * 服务端 list 面纪律 ≤8 标量元素，这里额外按 32 字符裁剪保 512B 载荷预算。 */
  selectedIds?: ReadonlyArray<string | number>;
}): void {
  // review P3-2：几何摘要只用 bbox（geometry 参数从未被消费 —— 删除，
  // 调用方不再白传全量几何）。
  const names = fieldNamesOf(info.properties);
  const digest = bboxDigest(info.bbox);
  const selectedIds = (info.selectedIds ?? [])
    .map((id) => String(id).slice(0, 32))
    .filter(Boolean)
    .slice(0, 8);
  emitInteraction('feature_selected', {
    layer_id: info.layerId,
    ...(info.featureId !== undefined ? { feature_id: String(info.featureId).slice(0, 128) } : {}),
    ...(names ? { field_names: names } : {}),
    ...(digest ? { geometry_digest: digest } : {}),
    ...(info.datasetRef ? { dataset_ref: info.datasetRef } : {}),
    ...(selectedIds.length > 1 ? { selected_ids: selectedIds } : {}),
  });
}

export function emitFeatureDeselected(info?: {
  layerId?: string;
  featureId?: string | number;
}): void {
  emitInteraction('feature_deselected', {
    ...(info?.layerId ? { layer_id: info.layerId } : {}),
    ...(info?.featureId !== undefined ? { feature_id: String(info.featureId).slice(0, 128) } : {}),
  });
}

export function emitAoiChanged(action: 'drawn' | 'edited' | 'deleted', geometry?: {
  type?: string;
  coordinates?: unknown;
} | null): void {
  const kind = action === 'drawn' ? 'aoi_drawn' : action === 'edited' ? 'aoi_edited' : 'aoi_deleted';
  if (action === 'deleted') {
    emitInteraction('aoi_deleted', {});
    return;
  }
  emitInteraction(kind, {
    ...(geometry?.type ? { geometry_type: geometry.type } : {}),
    ...(vertexCountOf(geometry) ? { vertex_count: vertexCountOf(geometry) } : {}),
    ...(geometryDigest(geometry) ? { geometry_digest: geometryDigest(geometry) } : {}),
  });
}

export function emitMeasurementCreated(info: {
  measureKind: 'distance' | 'area';
  value: number;
  unit: string;
  segmentCount?: number;
}): void {
  emitInteraction('measurement_created', {
    measure_kind: info.measureKind,
    value: Math.round(info.value * 10000) / 10000,
    unit: info.unit,
    ...(info.segmentCount !== undefined ? { segment_count: info.segmentCount } : {}),
  });
}

/**
 * mutation 链接型事件：在 mutation 落定后发射（意图在先 —— client_mutation_id
 * 把事件与那笔 mutation 绑定；结局如实申报，服务端还会对 _mutation_dedup
 * 做权威交叉校验 —— rejected/superseded 绝不投影为成功）。
 */
export function emitMutationLinked(
  kind: Extract<
    InteractionEventKind,
    | 'layer_visibility_changed'
    | 'layer_opacity_changed'
    | 'layer_style_changed'
    | 'layer_order_changed'
    | 'layer_removed'
  >,
  payload: Record<string, unknown>,
  refs: {
    clientMutationId: string;
    expectedRevision?: number;
    outcome: 'committed' | 'superseded' | 'failed';
    mutationRevision?: number;
  },
): void {
  emitInteraction(kind, payload, {
    client_mutation_id: refs.clientMutationId,
    ...(refs.expectedRevision !== undefined ? { expected_revision: refs.expectedRevision } : {}),
    mutation_outcome: refs.outcome,
    ...(refs.mutationRevision !== undefined ? { mutation_revision: refs.mutationRevision } : {}),
  });
}

export function emitComparisonModeChanged(enabled: boolean, mode?: string): void {
  emitInteraction('comparison_mode_changed', {
    enabled,
    ...(mode ? { mode } : {}),
  });
}
