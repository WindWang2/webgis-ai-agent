/**
 * 制图设计规范与系列作者（WP4）—— TS 类型面。
 *
 * 形状真相：
 *  - `/api/v1/design-specs` 已实现，行为真相 tests/unit/design_specs/test_design_spec_api.py；
 *  - `/api/v1/map-series` 为 WP3 并行契约（planning wp3_contract_brief.md §contract.py /
 *    §一致性 / §传播），端到端联调在其合并后进行 —— 类型照契约写，字段保持宽容
 *    （列表项带索引签名，UI 对缺失字段显示空态而非假数据）。
 *
 * 错误信封：HTTPException detail 被全局处理器放进响应体 `data`
 * （`{success:false, code, message, violations?}`）—— apiFetch 抛出的 ApiError
 * 携带整个 body，用 asDesignSeriesError 取 `body.data`（回退 `body.detail`）。
 */

// ── design_spec.v1 ──────────────────────────────────────────────────────────

export type DesignSpecStatus = 'draft' | 'published' | 'retired';

export interface DesignSpecViolation {
  field?: string;
  reason: string;
  message?: string;
}

export interface ReferenceValidation {
  ok: boolean;
  violations: DesignSpecViolation[];
  warnings: DesignSpecViolation[];
}

export interface DesignSpecPayload {
  spec_version: 'design_spec.v1';
  name: string;
  description?: string | null;
  /** 色彩：主题 + 顺序色带。 */
  color?: { theme_id: string; palette_sequential?: string | null } & Record<string, unknown>;
  /** 分类：默认方法 + 是否强制共享分级。 */
  classification?: { default_method: string; shared_required?: boolean } & Record<string, unknown>;
  /** 输出：目标集 + 纸张 profile。 */
  output?: { targets: string[]; paper_profile?: string | null } & Record<string, unknown>;
  /** 组件 / 角色 / 覆盖策略分节（后端契约开放结构，UI 按分节原样披露）。 */
  components?: Record<string, unknown>;
  roles?: Record<string, unknown>;
  overrides?: Record<string, unknown>;
}

export interface DesignSpecSummary {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  status: DesignSpecStatus;
  current_version: number;
  structural_digest: string;
  retired: boolean;
  retirement_note: string | null;
  creator_id: string;
  updated_at: string | null;
}

export interface DesignSpecVersion {
  id: string;
  spec_id: string;
  version: number;
  description: string | null;
  deprecated: boolean;
  parent_version_id: string | null;
  created_at?: string | null;
}

export interface DesignSpecDetail {
  success: boolean;
  spec: DesignSpecSummary;
  payload: DesignSpecPayload;
  reference_validation: ReferenceValidation;
}

export interface DesignSpecVersionResult {
  success: boolean;
  version: DesignSpecVersion;
  effective_payload: DesignSpecPayload;
  inheritance_chain: string[];
  reference_validation: ReferenceValidation;
}

export interface RenderTargets {
  supported: string[];
  unsupported: Array<{ target: string; reason: string }>;
  font_findings: Array<Record<string, unknown>>;
}

export interface DesignSpecConflict {
  code: string;
  message?: string;
  component?: string;
}

export interface DesignSpecPreview {
  success: boolean;
  spec: DesignSpecSummary;
  version: DesignSpecVersion;
  effective_payload: DesignSpecPayload;
  inheritance_chain: string[];
  reference_validation: ReferenceValidation;
  render_targets: RenderTargets;
  conflicts: DesignSpecConflict[];
  resolved_digest: string;
}

// ── map_series.v1（WP3 契约）────────────────────────────────────────────────

export type MapSeriesStatus = DesignSpecStatus;
export type SeriesMemberRole = 'primary' | 'comparison' | 'context' | 'detail';

export interface MapSeriesMember {
  member_id: string;
  title: string;
  subtitle?: string | null;
  session_id: string;
  role: SeriesMemberRole;
  dimension_tags: string[];
  extent_override?: [number, number, number, number] | null;
  data_bindings?: Record<string, string>;
  /** user-wins 锁：传播时相交字段 skip 并披露。 */
  locked_fields: string[];
  enabled: boolean;
}

export interface MapSeriesPayload {
  spec_version: 'map_series.v1';
  name: string;
  description?: string | null;
  design_spec_id: string;
  /** 可选版本 pin；缺省 = latest 解析时点记录。 */
  design_spec_version?: number | null;
  members: MapSeriesMember[];
  comparability?: Record<string, unknown>;
  dimension?: Record<string, unknown>;
  output?: Record<string, unknown>;
  /** 合成/测试数据披露（R1-P2；随冻结计划与系列 manifest 恒如实传播）。 */
  simulated_data?: boolean;
}

export interface MapSeriesSummary {
  id: string;
  name: string;
  description: string | null;
  status: MapSeriesStatus;
  current_version: number;
  structural_digest: string;
  design_spec_id: string | null;
  member_count?: number;
  is_builtin?: boolean;
  spec_version?: number | null;
  retired?: boolean;
  retirement_note?: string | null;
  creator_id?: string;
  updated_at: string | null;
}

export interface MapSeriesDetail {
  success: boolean;
  series: MapSeriesSummary;
  payload: MapSeriesPayload;
}

/** 封闭词表 code 的结构化 finding（WP3 consistency.py 契约）。 */
export type ConsistencySeverity = 'info' | 'warning' | 'error';

export interface ConsistencyFinding {
  code: string;
  severity: ConsistencySeverity;
  member_id?: string | null;
  component?: string | null;
  message: string;
  fix_hint?: string | null;
}

export interface ConsistencyResult {
  findings: ConsistencyFinding[];
  ok: boolean;
}

/** 传播 preview 单成员计划（app/services/map_series/propagation.py plan_member）。 */
export interface PropagationMemberPlan {
  member_id: string;
  session_id?: string | null;
  /** 成员会话 MapSpec 读不到 = 不可解析（如实披露）。 */
  unresolvable?: boolean;
  /** 命中 user-wins 锁的 intent 字段（palette/classification/…）。 */
  locked_fields_hit?: string[];
  changes?: Array<Record<string, unknown>>;
  skipped?: Array<Record<string, unknown>>;
  eligible?: boolean;
  current_revision?: number | null;
}

/**
 * 传播 preview（真实后端形状，WP3 已实现）：
 * `{success, series_id, member_count, unresolvable_member_ids, members}` ——
 * 受影响/锁定/不可解析三列由 members 投影派生。
 */
export interface PropagationPreviewResult {
  success?: boolean;
  series_id?: string | null;
  member_count?: number;
  unresolvable_member_ids: string[];
  members: PropagationMemberPlan[];
}

/** WP3 逐图 CAS receipt；skipped_locked = user-wins 锁跳过（如实披露）。 */
export type PropagationReceiptStatus = 'committed' | 'superseded' | 'failed' | 'skipped_locked';

export interface PropagationReceipt {
  member_id: string;
  status: PropagationReceiptStatus;
  revision?: number | string | null;
  error?: string | null;
}

export interface PropagationApplyResult {
  success?: boolean;
  series_id?: string | null;
  receipts: PropagationReceipt[];
  /** 逐状态计数（committed/superseded/failed/skipped_locked）。 */
  summary?: Record<string, number>;
  note?: string | null;
}

export interface FreezePlanResult {
  success?: boolean;
  freeze_plan: Record<string, unknown>;
}

/** 传播意图（WP3 typed 契约建议形状；空 intents = 由规范 pin 全量投影）。 */
export interface PropagationIntents {
  palette?: string | null;
  classification_method?: string | null;
  k?: number | null;
  components?: Array<{ component_type: string; op: 'ensure' | 'remove' | 'update'; options?: Record<string, unknown> }>;
  typography?: Record<string, unknown>;
}
