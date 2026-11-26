/**
 * Feature-edit 前端类型（/goal 03 + G14）——与后端
 * app/schemas/feature_edit_schema.py + feature_edit/service.summarize 的
 * 有界摘要对齐（手写镜像 + vitest 契约测试锁定关键字段）。
 */

/** 预览响应摘要（preview REST / 工具 plan_summary 镜像）。 */
export interface FeatureEditPreviewSummary {
  plan_id?: string;
  ref_id?: string;
  status?: string;
  committable?: boolean;
  safe?: boolean;
  n_targets?: number;
  ops?: FeatureEditOpSummary[];
  validation?: FeatureEditFinding[];
  expected_content_revision?: number;
  preview?: FeatureEditPreviewItem[];
  expires_in_s?: number;
  /** ADR-0300 共享拓扑编辑：impact 的有界摘要投影（仅共享 op 出现）。 */
  impact_summary?: FeatureEditImpactSummary;
  /** ADR-0300：基线/新增违规分离披露（仅共享 op 出现）。 */
  topology_context?: FeatureEditTopologyContext;
}

/** 单条编辑操作的服务端摘要（op_summaries 元素）。 */
export interface FeatureEditOpSummary {
  op:
    | 'set_fields'
    | 'rename_field'
    | 'repair_geometry'
    | 'move_by'
    | 'snap_to_ref'
    // G14 结构性操作
    | 'create_features'
    | 'delete_features'
    | 'replace_geometry'
    | 'split_feature'
    | 'merge_features'
    // ADR-0300 共享拓扑操作
    | 'move_shared_vertex'
    | 'insert_shared_vertex'
    | 'delete_shared_vertex'
    | 'reshape_shared_edge'
    | 'connect_endpoint';
  fields?: string[];
  from?: string;
  to?: string;
  fixes?: string[];
  dx_m?: number;
  dy_m?: number;
  snapped?: number;
  tolerance_m?: number;
  /** create_features 新增数量 / delete_features 删除数量。 */
  count?: number;
  /** replace_geometry 命中要素数。 */
  targets?: number;
  /** split_feature 目标身份 / 产出的子要素数。 */
  wfid?: string;
  children?: number;
  /** merge_features 父要素数 + 差异字段披露（first 策略）。 */
  parents?: number;
  conflict_fields?: string[];
  /** move_shared_vertex 触及的邻接要素数（服务端权威传播）。 */
  neighbors?: number;
}

/** 字段级 before/after（属性编辑的 diff 单元）。 */
export interface FeatureEditFieldDiff {
  before: unknown;
  after: unknown;
}

/** 单要素预览 diff（有界：属性全量 before/after；几何只给规模/bbox）。 */
export interface FeatureEditPreviewItem {
  wfid: string;
  fields?: Record<string, FeatureEditFieldDiff>;
  geometry?: {
    positions_before?: number;
    positions_after?: number;
    bbox_after?: [number, number, number, number] | null;
  };
  /** G14：结构性变化（要素出生/消失）。 */
  presence?: 'created' | 'removed';
}

/** 校验 finding（后端封闭 code 集）。 */
export interface FeatureEditFinding {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export type FeatureEditProposalStatus =
  | 'pending'
  | 'approving'
  | 'applied'
  | 'rejected'
  | 'stale'
  | 'conflicted'
  | 'expired'
  | 'error';

/** 提案卡状态（挂到助手消息，ClarificationCard 同款生命周期）。 */
export interface FeatureEditProposalState {
  plan_id: string;
  ref_id: string;
  status: FeatureEditProposalStatus;
  safe: boolean;
  n_targets: number;
  ops: FeatureEditOpSummary[];
  schema_ops: Array<{ from?: string; to?: string }>;
  preview: FeatureEditPreviewItem[];
  validation: FeatureEditFinding[];
  expected_content_revision: number;
  expires_in_s?: number;
  error?: string;
  /** ADR-0300：共享拓扑 plan 的有界影响摘要（仅共享 op 出现）。 */
  impact_summary?: FeatureEditImpactSummary;
  /** ADR-0300：基线/新增违规分离披露（仅共享 op 出现）。 */
  topology_context?: FeatureEditTopologyContext;
  /** applied 后的回执摘要（撤销入口/地图刷新依据）。 */
  receipt?: {
    entry_seq?: number;
    content_revision_after?: number;
    changed_wfids?: string[];
    stamp_status?: string;
    /** G14：本笔出生/退役的稳定身份（选择/标注失效面）。 */
    created_wfids?: string[];
    retired_wfids?: string[];
  };
}

/** G14：预览请求的操作载荷（封闭词表由后端 schema 拒绝未知项）。 */
export interface FeatureEditOpPayload {
  op: FeatureEditOpSummary['op'];
  fields?: Record<string, unknown>;
  fixes?: string[];
  dx_m?: number;
  dy_m?: number;
  ref_id?: string;
  tolerance_m?: number;
  from?: string;
  /** rename_field 目标字段名 / 共享 move 目标坐标 / connect 目标引用。 */
  to?: string | FeatureEditVertexRef;
  features?: Array<{ geometry: GeoJSON.Geometry; properties?: Record<string, unknown> }>;
  geometry?: GeoJSON.Geometry;
  wfid?: string;
  line?: GeoJSON.LineString;
  field_policy?: { mode?: 'first' | 'require_identical'; overrides?: Record<string, unknown> };
  // ── ADR-0300 共享拓扑操作参数（形状校验在服务端 _validate_op_shapes） ──
  /** move/insert/delete 的顶点引用：[lon,lat] 或 {wfid, path_index, vertex_index}。 */
  at?: FeatureEditVertexRef;
  /** insert_shared_vertex 的落点定位（{between:[a,b]} 或 {near, tolerance_m}）。 */
  edge?: { between?: [FeatureEditVertexRef, FeatureEditVertexRef]; near?: [number, number]; tolerance_m?: number };
  /** reshape_shared_edge 的新中间顶点序列（≥1 点）。 */
  path?: [number, number][];
  /** connect_endpoint 的端点引用。 */
  endpoint?: { wfid: string; end?: 'start' | 'end'; vertex_index?: number };
  /** insert：投影到边（默认 true）。 */
  snap_to_edge?: boolean;
  /** D1 层级守卫字段（声明值不同的顶点永不合并）。 */
  level_field?: string;
  /** G32 op 级目标限定（自然 id 或 WFID）。 */
  wfids?: string[];
}

/** 共享 op 的顶点引用（ADR-0300）：坐标或结构化索引。 */
export type FeatureEditVertexRef = [number, number] | { wfid: string; path_index?: number; vertex_index?: number };

/**
 * ADR-0300：plan impact 的有界摘要（summarize_plan 投影，仅共享 op 出现）。
 * affected_neighbors 已在服务端有界（≤64 + truncated 披露）。
 */
export interface FeatureEditImpactSummary {
  touched_features?: number | null;
  affected_neighbors?: number;
  neighbors_truncated?: boolean;
  area_delta_m2?: number | null;
  length_delta_m?: number | null;
  atomicity?: { scope?: string; cross_dataset?: string; note?: string } | null;
}

/** ADR-0300：基线/新增违规分离披露（D4：新增阻断、存量只披露）。 */
export interface FeatureEditTopologyContext {
  new_violations?: number | null;
  existing_violations?: number | null;
  rules?: string[];
}

/** 邻接受影响对象（索引成员关系判定，非 bbox 近似）。 */
export interface FeatureEditNeighborView {
  wfid: string;
  relation: 'shares_edge' | 'incident_node';
  shared_with: string[];
}

/** 悬挂端点（线网仅一条线接入的端点节点，辅助数字化定位）。 */
export interface FeatureEditDanglingEndpoint {
  wfid: string;
  coordinate: [number, number];
}

/**
 * ADR-0300：约束面检查响应（constraints/inspect 只读投影）。
 * 索引对物化形态构建；typed 否定面（index_too_large 等）在 index_findings 照实披露。
 */
export interface FeatureEditConstraintsInspectResult {
  success?: boolean;
  ref_id?: string;
  match_mode?: 'exact' | 'tolerance';
  tolerance_m?: number | null;
  level_field?: string | null;
  indexed_features?: number;
  excluded_features?: Record<string, string>;
  vertex_count?: number;
  shared_edges?: number;
  junction_nodes_by_feature_count?: Record<string, number>;
  dangling_endpoints?: FeatureEditDanglingEndpoint[];
  neighbors?: FeatureEditNeighborView[];
  declared_rules?: unknown[];
  baseline_findings?: FeatureEditFinding[];
  baseline_total?: number;
  index_findings?: FeatureEditFinding[];
  /** 索引/比较预算披露（超限 typed 拒绝；形状开放键值）。 */
  budgets?: Record<string, unknown>;
}

/** ADR-0300：候选 op 的有界影响投影（impact REST，纯读不落 plan store）。 */
export interface FeatureEditImpactResult {
  success?: boolean;
  ref_id?: string;
  committable?: boolean;
  findings?: FeatureEditFinding[];
  op_summaries?: FeatureEditOpSummary[];
  touched_features?: number | null;
  affected_neighbors?: FeatureEditNeighborView[];
  neighbors_truncated?: boolean;
  measure_delta?: {
    touched?: Array<{
      wfid: string;
      area_before_m2?: number;
      area_after_m2?: number;
      length_before_m?: number;
      length_after_m?: number;
    }>;
    area_delta_m2?: number;
    length_delta_m?: number;
  };
  new_violations?: number | null;
  existing_violations?: number | null;
  atomicity?: { scope?: string; cross_dataset?: string; note?: string };
}

/** ADR-0300：声明规则在当前修订版的复检（revalidate REST，编辑后质量闭环）。 */
export interface FeatureEditRevalidateResult {
  success?: boolean;
  ref_id?: string;
  rules?: unknown[];
  findings?: FeatureEditFinding[];
  baseline_total?: number;
  checked_wfids?: string[];
  note?: string;
}

/** G14：provisioning 响应（datasets create / promote-sketch / editable-copy）。 */
export interface FeatureEditProvisionResult {
  ok: boolean;
  ref_id: string;
  layer_id: string | null;
  name: string;
  geometry_type?: string;
  topology?: { rules?: string[]; tolerance_m?: number } | null;
  feature_count?: number;
  content_revision?: number;
  origin_kind?: string;
  derived_from?: string;
  source_was_read_only?: boolean;
}

/** G14：编辑资格判定（「开始编辑」按钮依据）。 */
export interface FeatureEditQualification {
  ref_id: string;
  editable: boolean;
  reason: string | null;
  read_only: boolean;
  feature_count: number;
  geometry_types: string[];
  declared_geometry_type: string | null;
  content_revision: number;
  topology: { rules?: string[]; tolerance_m?: number } | null;
  has_wfids: boolean;
}

/** G14：血缘事件（旧引用失效/迁移的公开投影）。 */
export interface FeatureEditLineageEvent {
  seq: number;
  kind: 'split' | 'merge';
  parent_wfids: string[];
  child_wfids: string[];
  content_revision_after?: number;
}

/** REST decision 响应（approve 成功 / 冲突 / 过期）。 */
export interface FeatureEditDecisionResult {
  status: 'applied' | 'rejected';
  receipt?: FeatureEditProposalState['receipt'];
}

export interface FeatureEditUndoResult {
  status: 'undone' | 'redone';
  receipt?: FeatureEditProposalState['receipt'];
}

export interface FeatureEditHistoryEntry {
  seq: number;
  kind: 'apply' | 'undo' | 'redo';
  actor?: string;
  origin?: string;
  changed: number;
  content_revision_after?: number;
}
