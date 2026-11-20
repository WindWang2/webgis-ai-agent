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
    | 'merge_features';
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
  to?: string;
  features?: Array<{ geometry: GeoJSON.Geometry; properties?: Record<string, unknown> }>;
  geometry?: GeoJSON.Geometry;
  wfid?: string;
  line?: GeoJSON.LineString;
  field_policy?: { mode?: 'first' | 'require_identical'; overrides?: Record<string, unknown> };
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
