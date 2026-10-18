/**
 * ClarificationEnvelope 前端类型（方向 3 / ADR-0222）。
 *
 * 与后端 `app/services/gis_harness/requirement_ir/envelope.py` 的
 * `clarification_envelope.v1` 逐字段对应（wire 契约，additive）。
 * envelope 从 RequirementDocument 派生：request_id 绑定
 * document_id + requirement_revision + context_keys —— 应答时原样回传。
 */

export interface ClarificationOption {
  value: string;
  label_zh: string;
  label_en: string;
  is_default: boolean;
}

export interface ClarificationQuestion {
  code: string;
  context_key: string;
  path: string;
  question_zh: string;
  question_en: string;
  options: ClarificationOption[];
  allow_free_text: boolean;
  can_waive: boolean;
  default_value: string;
  default_rationale: string;
  reason_summary: string;
}

export interface ClarificationEnvelope {
  schema_version: string;
  request_id: string;
  session_id: string;
  document_id: string;
  requirement_revision: number;
  raised_turn_id: string;
  questions: ClarificationQuestion[];
  defaultable: boolean;
}

/** 卡片生命周期：open → submitting → submitted；stale = revision 已前进（需重取）。 */
export type ClarificationStatus = 'open' | 'submitting' | 'submitted' | 'stale' | 'error';

export interface ClarificationCardState {
  envelope: ClarificationEnvelope;
  status: ClarificationStatus;
  /** error/stale 时的本地化提示（i18n key 或服务端 code）。 */
  errorCode?: string;
  /** 提交成功后回显的答案（context_key → 用户值/默认值标记）。 */
  submittedAnswers?: Record<string, string>;
}

export interface ClarificationAnswerItem {
  context_key: string;
  value: string;
}

export interface ClarificationAnswerResult {
  status: 'answered' | 'duplicate';
  resolved_all: boolean;
  still_pending: string[];
  applied_patches: number;
  default_used: boolean;
}
