/**
 * Interaction IR V1 —— 前端契约类型与常量（ADR-0225）。
 *
 * 权威词表在 `app/services/gis_harness/interaction_ir/contracts.py`；本文件
 * 是浏览器端消费的镜像（kind 词表 / 合并策略 / 有界常量），由
 * `tests/unit/gis_harness/interaction_ir/contract_manifest.json` 双向漂移闸
 * 锁定（backend pytest ≡ JSON ≡ 本文件，carto_ir manifest 模式）。
 *
 * 纪律：Interaction IR 是观察层 —— 用户 layer mutation 仍走 MapSpec
 * lifecycle；事件只记录发生了什么 + 与哪笔 mutation/revision 关联。
 */

/** 事件信封版本（= 后端 SCHEMA_VERSION）。 */
export const INTERACTION_SCHEMA_VERSION = 'interaction_ir.v1' as const;

export type InteractionActor = 'user' | 'agent' | 'system';

export type InteractionMutationOutcome = 'committed' | 'superseded' | 'failed';

/** 合并策略：none | replace_pending（队列内同 kind 只保留最新待发）。 */
export type CoalescePolicy = 'none' | 'replace_pending';

export interface InteractionEventRefs {
  /** 引发本事件的上一事件/mutation id（causality 链）。 */
  causation_id?: string;
  correlation_id?: string;
  turn_id?: string;
  /** 用户 mutation 的客户端幂等键（服务端以 c:<id> 交叉校验）。 */
  client_mutation_id?: string;
  expected_revision?: number;
  /** 客户端申报的 mutation 结局（服务端对 _mutation_dedup 校验）。 */
  mutation_revision?: number;
  mutation_outcome?: InteractionMutationOutcome;
  mapspec_fingerprint?: string;
  /** F4 viewport 双写方序（仅 viewport_settled 携带）。 */
  viewport_seq?: number;
  render_observation_seq?: number;
}

export interface InteractionEventV1 extends InteractionEventRefs {
  schema_version: typeof INTERACTION_SCHEMA_VERSION;
  event_id: string;
  actor: InteractionActor;
  /** per-tab 稳定实例 id（sessionStorage 维持跨刷新连续性）。 */
  client_instance_id: string;
  /** per-(session, client_instance_id) 严格单调，从 1 起。 */
  client_seq: number;
  kind: InteractionEventKind;
  payload: Record<string, unknown>;
  /** advisory —— 排序/投影/指纹一律不用 wall-clock。 */
  created_at?: string;
}

export type InteractionEventKind =
  | 'viewport_settled'
  | 'feature_selected'
  | 'feature_deselected'
  | 'aoi_drawn'
  | 'aoi_edited'
  | 'aoi_deleted'
  | 'measurement_created'
  | 'layer_visibility_changed'
  | 'layer_opacity_changed'
  | 'layer_style_changed'
  | 'layer_order_changed'
  | 'layer_removed'
  | 'comparison_mode_changed';

/** kind → 合并策略（= 后端 INTERACTION_KINDS[].coalesce 的镜像）。 */
export const KIND_COALESCE_POLICY: Record<InteractionEventKind, CoalescePolicy> = {
  viewport_settled: 'replace_pending',
  feature_selected: 'none',
  feature_deselected: 'none',
  aoi_drawn: 'none',
  aoi_edited: 'none',
  aoi_deleted: 'none',
  measurement_created: 'none',
  layer_visibility_changed: 'none',
  layer_opacity_changed: 'none',
  layer_style_changed: 'none',
  layer_order_changed: 'none',
  layer_removed: 'none',
  comparison_mode_changed: 'none',
};

/** 单批事件上限（= 后端 MAX_EVENTS_PER_BATCH）。 */
export const MAX_EVENTS_PER_BATCH = 50;

/** 服务端 ack 单条裁决。 */
export type InteractionVerdict =
  | 'accepted'
  | 'accepted_with_gap'
  | 'duplicate'
  | 'stale_seq'
  | 'rejected';

export interface InteractionEventAck {
  event_id: string;
  client_seq: number;
  verdict: InteractionVerdict;
  reason?: string;
  server_seq?: number;
  gap_from?: number;
}

export interface InteractionIngestResponse {
  accepted: number;
  duplicates: number;
  stale: number;
  rejected: number;
  acks: InteractionEventAck[];
  /** client_instance_id → 服务端已见最大 client_seq。 */
  watermarks?: Record<string, number>;
  dropped?: number;
  persist_degraded?: boolean;
}
