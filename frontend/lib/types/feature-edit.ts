/**
 * Feature-edit 前端类型（/goal 03）——与后端
 * app/schemas/feature_edit_schema.py + feature_edit/service.summarize 的
 * 有界摘要对齐（手写镜像 + vitest 契约测试锁定关键字段）。
 */

/** 单条编辑操作的服务端摘要（op_summaries 元素）。 */
export interface FeatureEditOpSummary {
  op: 'set_fields' | 'rename_field' | 'repair_geometry' | 'move_by' | 'snap_to_ref';
  fields?: string[];
  from?: string;
  to?: string;
  fixes?: string[];
  dx_m?: number;
  dy_m?: number;
  snapped?: number;
  tolerance_m?: number;
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
  };
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
