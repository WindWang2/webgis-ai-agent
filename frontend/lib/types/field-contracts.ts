/**
 * G44 业务字段合同前端类型 —— 与后端
 * app/schemas/field_contracts_schema.py + field_contracts/service 的
 * 有界视图手写镜像（snake_case wire；vitest 契约测试锁定路径/参数形状）。
 *
 * 语义纪律（与后端注释一致）：
 * - required 与 nullable 正交：required 只约束「缺字段」；nullable 约束
 *   显式 null；空串政策只对 string 生效（reject/allow）；
 * - default 只用于 create 表单初始化 + 显式披露，不静默改已存字段；
 * - enum ≤ MAX_ENUM_VALUES（256），observed 词汇是证据不是规则。
 */

/** 字段类型封闭词表（后端 FIELD_TYPE_NAMES）。 */
export type FieldContractFieldType =
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'date'
  | 'datetime';

export type FieldContractStatus = 'draft' | 'active' | 'retired' | 'superseded';

/** 表单控件提示（缺省按 type 推导）。 */
export type FieldContractFormControl =
  | 'text'
  | 'textarea'
  | 'select'
  | 'date'
  | 'datetime-local';

/** 单字段声明（严格标量 + 取值域 + 展示元数据）。 */
export interface FieldContractFieldRule {
  field: string;
  type: FieldContractFieldType;
  required: boolean;
  nullable: boolean;
  empty_string: 'reject' | 'allow';
  enum?: string[] | null;
  min?: number | null;
  max?: number | null;
  exclusive_min?: boolean;
  exclusive_max?: boolean;
  min_length?: number | null;
  max_length?: number | null;
  label?: string | null;
  description?: string | null;
  unit?: string | null;
  /** create 表单默认值（unknown：布尔/数值/字符串皆可；null=无默认）。 */
  default?: unknown;
  form_control?: FieldContractFormControl | null;
}

export type FieldContractConditionOp = 'eq' | 'ne' | 'in' | 'not_in' | 'gt' | 'gte' | 'lt' | 'lte';
export type FieldContractCompareOp = 'lt' | 'lte' | 'gt' | 'gte';

export interface FieldContractConditionWhen {
  field: string;
  op: FieldContractConditionOp;
  value?: unknown;
  values?: unknown[] | null;
}

/** 跨字段封闭规则：conditional_required | compare | unique。 */
export interface FieldContractCrossRule {
  rule_id: string;
  kind: 'conditional_required' | 'compare' | 'unique';
  // conditional_required
  when?: FieldContractConditionWhen | null;
  require?: string[] | null;
  // compare
  left?: string | null;
  op?: FieldContractCompareOp | null;
  right?: string | null;
  // unique
  fields?: string[] | null;
  null_policy?: 'ignore' | 'participate';
  note?: string | null;
}

/** 合同声明正文（版本化不可变内容；canonical digest 的输入）。 */
export interface FieldContractDocument {
  version: 1;
  title: string;
  notes?: string | null;
  fields: FieldContractFieldRule[];
  cross_rules: FieldContractCrossRule[];
}

/** 激活收据（binding.activation / contract.activation 同形）。 */
export interface FieldContractActivation {
  actor?: string | null;
  activated_at?: string | null;
  assessment_id?: string | null;
  data_digest?: string | null;
  content_revision?: number | null;
  rules_digest?: string | null;
  violations?: number | null;
  completeness?: string | null;
}

/** 合同完整视图（list/get/create/revise 返回）。 */
export interface FieldContractView {
  contract_id: string;
  contract_key: string;
  session_id: string;
  ref_id: string;
  status: FieldContractStatus;
  revision: number;
  title: string;
  rules: FieldContractDocument;
  normalized_digest: string;
  binding?: { activation?: FieldContractActivation } | null;
  activation?: FieldContractActivation | null;
  notes?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  dataset?: Record<string, unknown> | null;
}

export type FieldAssessmentStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'incomplete';

/** 评估校验指纹（激活闸门比对 rules_digest 的依据）。 */
export interface FieldAssessmentVerified {
  data_digest?: string | null;
  content_revision?: number | null;
  features_total?: number | null;
  rules_digest?: string | null;
}

export interface FieldAssessmentCounts {
  total_features?: number;
  checked_features?: number;
  violations_total?: number;
  overflow_count?: number;
  counts_by_code?: Record<string, number>;
  counts_by_rule?: Record<string, number>;
  unique_conflict_groups?: number;
  complete?: boolean;
  [k: string]: unknown;
}

/** 评估视图（startAssessment.assessment / GET assessment）。 */
export interface FieldAssessmentView {
  id: string;
  status: FieldAssessmentStatus;
  verified?: FieldAssessmentVerified | null;
  counts?: FieldAssessmentCounts | null;
  findings_path?: string | null;
  sampled?: boolean | null;
  error?: string | null;
  [k: string]: unknown;
}

/** POST /{contract_id}/assess 响应。 */
export interface FieldAssessStartResult {
  assessment: FieldAssessmentView;
  job_id?: number | string | null;
  poll?: string | null;
}

/** 单条违规 finding（findings 分页元素；code 为后端封闭集合）。 */
export interface FieldContractFinding {
  code: string;
  rule_id?: string | null;
  field?: string | null;
  wfid?: string | null;
  row?: number | null;
  value?: unknown;
  fields?: Record<string, unknown> | string[] | null;
  group_size?: number | null;
  detail?: Record<string, unknown> | string | null;
}

/** findings 分页（cursor 透传；overflow_note 在物化截断时出现）。 */
export interface FieldFindingsPageView {
  findings: FieldContractFinding[];
  total_violations: number;
  filtered_total: number;
  next_cursor: string | null;
  truncated_page_note?: string | null;
  overflow_note?: string | null;
  sampled?: boolean | null;
}

/** observe 端点：observed 字段 → 观测类型计数（可选 enum 词汇）。 */
export interface FieldObservedField {
  name: string;
  types: Record<string, number>;
  enum?: string[] | null;
}

/** observe 端点：当前合同（或无合同时 schema 推断）的声明侧投影。 */
export interface FieldDeclaredField {
  field: string;
  type: FieldContractFieldType | string;
  required: boolean;
  nullable: boolean;
  enum?: string[] | null;
  unit?: string | null;
  label?: string | null;
}

/** GET /datasets/{ref_id}/observe 响应。 */
export interface FieldObserveView {
  ref_id: string;
  feature_count: number;
  observed: Record<string, FieldObservedField>;
  declared: FieldDeclaredField[];
  declared_source: { contract_id: string; status: FieldContractStatus; revision: number } | null;
  note?: string | null;
}

/** binding 端点的合同摘要（无 rules —— 取正文走 getContract）。 */
export interface FieldBindingContract {
  id: string;
  contract_key: string;
  revision: number;
  digest: string;
  title: string;
  activation?: FieldContractActivation | null;
}

/** GET /datasets/{ref_id}/binding 响应。 */
export interface FieldBindingStatusView {
  status: 'ok' | 'stale' | 'missing' | 'unavailable';
  contract: FieldBindingContract | null;
}

/** POST activate / retire 响应。 */
export interface FieldContractMutationResult {
  activated?: boolean;
  retired?: boolean;
  contract: FieldContractView;
}
