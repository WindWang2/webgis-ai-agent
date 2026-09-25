/**
 * RenderApplyAck — desired→apply→ACK 事务语义的前端投影（F13，ADR-0214 D3）。
 *
 * 纪律（与 layer-status.ts 同款）：
 *
 * - **纯投影，零状态**：ACK 是 (desired spec, applied spec, reconcile
 *   事实, pending 事实) 的只读派生 —— 绝不写平行状态，绝不反写 MapSpec；
 * - **user-wins 弃权**：pending 用户操作（显隐/透明度乐观 patch、删除）
 *   涉及的图层**不进 ACK** —— 中间态既不是 applied 也不是 failed，
 *   把它归因为任何一种都是对用户编辑的僭越；
 * - **词表镜像**：reason code 镜像后端
 *   `app/lib/cartography/render_apply_ack.py`（后端是单一权威：未知码
 *   在服务端降级 apply_error 并审计 —— 前端漂移只会降级，不会误报）；
 * - **有界**：层 ≤64 / 组件 ≤32，超限截断 + `partial_apply.disclosed`。
 *
 * 载体：随 RenderObservation POST 的 `apply_ack` 块上行（增维不换通道）；
 * stale 对账（`isStaleApplyAck`）+ 服务端 stamped revision 门共同保证
 * 「stale ACK 不覆盖新状态」。
 */

import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { layerAliases } from '@/lib/mapspec/live-spec';

export const APPLY_ACK_SCHEMA_VERSION = 'render_apply_ack.v1' as const;

/** 事务级 status（封闭词表，镜像后端 ACK_STATUSES）。 */
export type ApplyAckStatus = 'applied' | 'partial' | 'failed';

/** 条目级 status（封闭词表，镜像后端 ENTRY_STATUSES）。 */
export type ApplyAckEntryStatus = 'applied' | 'failed' | 'skipped' | 'pending';

/** 封闭 reason code 词表（镜像后端 REASON_CODES —— 后端单一权威）。 */
export type ApplyAckReasonCode =
  | 'missing_after_apply'
  | 'unsupported_layer_type'
  | 'source_unresolved'
  | 'style_diverged'
  | 'apply_error'
  | 'user_pending'
  | 'budget_degraded';

export interface ApplyAckLayerEntry {
  layer_id: string;
  status: ApplyAckEntryStatus;
  reason_code?: ApplyAckReasonCode;
}

export interface ApplyAckComponentEntry {
  component_id: string;
  status: ApplyAckEntryStatus;
  reason_code?: ApplyAckReasonCode;
}

export interface RenderApplyAck {
  schema_version: typeof APPLY_ACK_SCHEMA_VERSION;
  mapspec_revision: number;
  status: ApplyAckStatus;
  layers: ApplyAckLayerEntry[];
  components: ApplyAckComponentEntry[];
  partial_apply: { discarded: number };
  /** 有界 reconcile 错误文本（advisory —— 不单独作为失败归因）。 */
  reconcile_error: string;
}

/** 有界预算（与后端 MAX_ACK_LAYERS / MAX_ACK_COMPONENTS 同值契约）。 */
export const MAX_ACK_LAYERS = 64;
export const MAX_ACK_COMPONENTS = 32;

/**
 * 当前 runtime 可渲染的图层类型集合 —— 编译器（compiler.ts）逐类型分支
 * 的实况。类型级穷举守卫：generated 词表新增成员时 `satisfies` 检查
 * 编译失败，集合与词表不可能静默漂移。
 */
type SpecLayerType = import('@/lib/mapspec-compiler/types.generated').MapSpecLayer['type'];
const SUPPORTED_LAYER_TYPES = new Set<SpecLayerType>([
  'circle', 'line', 'fill', 'symbol', 'heatmap',
  'raster', 'fill-extrusion', 'background', 'hillshade',
]);
const _exhaustiveGuard = {
  circle: true, line: true, fill: true, symbol: true, heatmap: true,
  raster: true, 'fill-extrusion': true, background: true, hillshade: true,
} satisfies Record<SpecLayerType, true>;
void _exhaustiveGuard;

export interface BuildApplyAckOptions {
  spec: MapSpec;
  /** MapSpecRuntime.getAppliedSpec()（实际挂载面；null = 无 apply 结果）。 */
  applied: MapSpec | null | undefined;
  /** 会话游标 revision（本 ACK 对账的 desired revision）。 */
  revision: number;
  /** MapSpecRuntime.getLastError()（advisory 披露，截断 160 字符）。 */
  reconcileError?: string;
  /** bounded settle 结果 —— false 时缺失层报 pending 而非 failed。 */
  mapIdle: boolean;
  /** pending 用户操作键集（getPendingPresentation() 的 keys，含别名）。 */
  pendingLayerIds?: ReadonlySet<string>;
  /** pending 删除集（getPendingRemoved()）。 */
  pendingRemovedIds?: readonly string[];
  /** 观察到的 mounted 组件 id 集（observeComponents 派生）。 */
  mountedComponentIds?: ReadonlySet<string>;
}

function aliasesOf(id: string): string[] {
  try {
    return layerAliases(id);
  } catch {
    return [id];
  }
}

function isPendingTouched(
  id: string,
  pending?: ReadonlySet<string>,
  removed?: readonly string[],
): boolean {
  if (removed && removed.length > 0) {
    for (const alias of aliasesOf(id)) {
      if (removed.includes(alias)) return true;
    }
  }
  if (pending && pending.size > 0) {
    for (const alias of aliasesOf(id)) {
      if (pending.has(alias)) return true;
    }
  }
  return false;
}

function appliedIds(spec: MapSpec | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const layer of spec?.layers ?? []) {
    const id = String(layer.id || '');
    if (!id) continue;
    for (const alias of aliasesOf(id)) out.add(alias);
  }
  return out;
}

/**
 * desired→apply→ACK 纯投影。层序 = desired spec 顺序（稳定、可对账）。
 * deriveLayerStatus 的 ACK 面同源语义：
 *
 *   type 不支持            → skipped + unsupported_layer_type
 *   applied 缺             → mapIdle ? failed + missing_after_apply
 *                            : pending（settle 未落定 —— 非失败，非终态）
 *   applied 有但源缺席     → failed + source_unresolved（background 豁免：
 *                            source="" 哨兵无数据面）
 *   其余                   → applied
 */
export function buildRenderApplyAck({
  spec,
  applied,
  revision,
  reconcileError = '',
  mapIdle,
  pendingLayerIds,
  pendingRemovedIds,
  mountedComponentIds,
}: BuildApplyAckOptions): RenderApplyAck {
  const layers: ApplyAckLayerEntry[] = [];
  let discarded = 0;
  let failedCount = 0;
  let skippedCount = 0;
  let appliedCount = 0;
  let pendingCount = 0;

  const appliedSet = appliedIds(applied);
  const appliedSources = new Set<string>(
    Object.keys(applied?.sources ?? {}),
  );

  for (const layer of spec?.layers ?? []) {
    if (layers.length >= MAX_ACK_LAYERS) {
      discarded += 1;
      continue;
    }
    const id = String(layer.id || '');
    if (!id) continue;
    // user-wins 弃权：pending 用户操作涉及的层不进 ACK。
    if (isPendingTouched(id, pendingLayerIds, pendingRemovedIds)) continue;

    const ltype = layer.type as SpecLayerType;
    if (!SUPPORTED_LAYER_TYPES.has(ltype)) {
      skippedCount += 1;
      layers.push({
        layer_id: id,
        status: 'skipped',
        reason_code: 'unsupported_layer_type',
      });
      continue;
    }
    if (!appliedSet.has(id)) {
      if (mapIdle) {
        failedCount += 1;
        layers.push({
          layer_id: id,
          status: 'failed',
          reason_code: 'missing_after_apply',
        });
      } else {
        pendingCount += 1;
        layers.push({ layer_id: id, status: 'pending' });
      }
      continue;
    }
    // background 层 source="" 哨兵无数据面 —— 源收敛检查豁免。
    const sourceId = String(layer.source || '');
    if (ltype !== 'background' && sourceId && !appliedSources.has(sourceId)) {
      failedCount += 1;
      layers.push({
        layer_id: id,
        status: 'failed',
        reason_code: 'source_unresolved',
      });
      continue;
    }
    appliedCount += 1;
    layers.push({ layer_id: id, status: 'applied' });
  }

  const components: ApplyAckComponentEntry[] = [];
  let discardedComponents = 0;
  // chrome 观察集为空（未挂载/无 renderable 组件）= 无观察基准 —— 缺席
  // 只能 pending（诚实缺席），绝不虚构 failed。
  const hasMountedBasis = !!mountedComponentIds && mountedComponentIds.size > 0;
  for (const comp of spec?.layout?.components ?? []) {
    if (components.length >= MAX_ACK_COMPONENTS) {
      discardedComponents += 1;
      continue;
    }
    if (!comp || comp.enabled === false) continue;
    const cid = String(comp.id || '');
    if (!cid) continue;
    const mounted = mountedComponentIds?.has(cid) ?? false;
    if (mounted) {
      components.push({ component_id: cid, status: 'applied' });
      continue;
    }
    if (hasMountedBasis) {
      // chrome 在场但缺该组件：settle 未落定 → pending；落定 → 失败。
      if (mapIdle) {
        failedCount += 1;
        components.push({
          component_id: cid,
          status: 'failed',
          reason_code: 'missing_after_apply',
        });
      } else {
        pendingCount += 1;
        components.push({ component_id: cid, status: 'pending' });
      }
      continue;
    }
    // 无观察基准（chrome 未挂载）：组件挂载事实未知 → 不计入失败。
    pendingCount += 1;
    components.push({ component_id: cid, status: 'pending' });
  }

  const status: ApplyAckStatus =
    failedCount > 0
      ? (appliedCount > 0 ? 'partial' : 'failed')
      : (pendingCount > 0 || skippedCount > 0 || discarded > 0
          ? 'partial'
          : 'applied');

  return {
    schema_version: APPLY_ACK_SCHEMA_VERSION,
    mapspec_revision: Math.max(0, Math.floor(revision) || 0),
    status,
    layers,
    components,
    partial_apply: { discarded: discarded + discardedComponents },
    reconcile_error: String(reconcileError || '').slice(0, 160),
  };
}

/**
 * Stale ACK 判定：ACK 对账的 revision 落后于当前 committed revision →
 * stale（调用方丢弃，不覆盖新状态 —— 与后端 stamped revision 门双向
 * 收敛；同 revision 重复 ACK 是幂等重放，不算 stale）。
 */
export function isStaleApplyAck(ack: RenderApplyAck, currentRevision: number): boolean {
  return ack.mapspec_revision < currentRevision;
}
