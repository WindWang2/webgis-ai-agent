/**
 * Field contracts REST 客户端（G44 业务字段合同）—— apiFetch 双头鉴权，
 * 与后端 app/api/routes/field_contracts.py 端点族一一对应。
 *
 * 诚实纪律：
 * - ref_id 含 ':'（dataset:xxx 形态），路径段一律 encodeURIComponent；
 * - 错误统一经 asFieldContractError 提取后端 typed 码
 *   （detail={code,message,detail}；revision_conflict / assessment_stale 等
 *   由 UI 按 code 收敛，不解析文案）；
 * - findings 分页 cursor 透传（next_cursor=null 即末页），code 过滤可选。
 */
import { apiFetch } from '@/lib/api/transport';
import type {
  FieldAssessmentView,
  FieldAssessStartResult,
  FieldBindingStatusView,
  FieldContractDocument,
  FieldContractMutationResult,
  FieldContractView,
  FieldFindingsPageView,
  FieldObserveView,
} from '@/lib/types/field-contracts';

const BASE = (sessionId: string) =>
  `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`;

/** GET /field-contracts?ref_id= —— ref_id 可选（缺省列全会话合同）。 */
export function listContracts(
  sessionId: string,
  refId?: string | null,
  ownerToken?: string | null,
): Promise<{ contracts: FieldContractView[] }> {
  const q = refId ? `?ref_id=${encodeURIComponent(refId)}` : '';
  return apiFetch(`${BASE(sessionId)}/field-contracts${q}`, {
    ownerToken,
    label: 'FieldContracts',
  });
}

export function getContract(
  sessionId: string,
  contractId: string,
  ownerToken?: string | null,
): Promise<FieldContractView> {
  return apiFetch(`${BASE(sessionId)}/field-contracts/${encodeURIComponent(contractId)}`, {
    ownerToken,
    label: 'FieldContracts',
  });
}

/** POST /field-contracts —— 400 draft_exists / invalid_document。 */
export function createContract(
  sessionId: string,
  refId: string,
  document: FieldContractDocument,
  ownerToken?: string | null,
): Promise<FieldContractView> {
  return apiFetch(`${BASE(sessionId)}/field-contracts`, {
    method: 'POST',
    body: { ref_id: refId, document },
    ownerToken,
    label: 'FieldContracts',
  });
}

/** PUT —— revision CAS；409/400 revision_conflict 时 UI 须重拉草稿。 */
export function reviseContract(
  sessionId: string,
  contractId: string,
  expectedRevision: number,
  document: FieldContractDocument,
  ownerToken?: string | null,
): Promise<FieldContractView> {
  return apiFetch(`${BASE(sessionId)}/field-contracts/${encodeURIComponent(contractId)}`, {
    method: 'PUT',
    body: { expected_revision: expectedRevision, document },
    ownerToken,
    label: 'FieldContracts',
  });
}

/** POST assess —— 后台 job；轮询走 getAssessment（poll 路径仅披露）。 */
export function startAssessment(
  sessionId: string,
  contractId: string,
  ownerToken?: string | null,
): Promise<FieldAssessStartResult> {
  return apiFetch(`${BASE(sessionId)}/field-contracts/${encodeURIComponent(contractId)}/assess`, {
    method: 'POST',
    ownerToken,
    label: 'FieldContracts',
  });
}

export function getAssessment(
  sessionId: string,
  assessmentId: string,
  ownerToken?: string | null,
): Promise<FieldAssessmentView> {
  return apiFetch(
    `${BASE(sessionId)}/field-contracts-assessments/${encodeURIComponent(assessmentId)}`,
    { ownerToken, label: 'FieldContracts' },
  );
}

export interface FindingsPageQuery {
  cursor?: string | null;
  limit?: number;
  code?: string | null;
}

/** GET findings —— cursor/limit/code 查询参数（limit 1..100）。 */
export function findingsPage(
  sessionId: string,
  assessmentId: string,
  query: FindingsPageQuery = {},
  ownerToken?: string | null,
): Promise<FieldFindingsPageView> {
  const params = new URLSearchParams();
  if (query.cursor) params.set('cursor', query.cursor);
  if (typeof query.limit === 'number') params.set('limit', String(query.limit));
  if (query.code) params.set('code', query.code);
  const q = params.toString();
  return apiFetch(
    `${BASE(sessionId)}/field-contracts-assessments/${encodeURIComponent(assessmentId)}/findings${q ? `?${q}` : ''}`,
    { ownerToken, label: 'FieldContracts' },
  );
}

/** GET observe —— observed（类型计数/enum 词汇）vs declared 对照。 */
export function observeFields(
  sessionId: string,
  refId: string,
  ownerToken?: string | null,
): Promise<FieldObserveView> {
  return apiFetch(
    `${BASE(sessionId)}/field-contracts/datasets/${encodeURIComponent(refId)}/observe`,
    { ownerToken, label: 'FieldContracts' },
  );
}

/** GET binding —— ok/stale/missing + active 合同摘要（无 rules）。 */
export function bindingStatus(
  sessionId: string,
  refId: string,
  ownerToken?: string | null,
): Promise<FieldBindingStatusView> {
  return apiFetch(
    `${BASE(sessionId)}/field-contracts/datasets/${encodeURIComponent(refId)}/binding`,
    { ownerToken, label: 'FieldContracts' },
  );
}

/** POST activate —— 400 no_activatable_assessment / assessment_stale；409 activation_race。 */
export function activateContract(
  sessionId: string,
  contractId: string,
  ownerToken?: string | null,
): Promise<FieldContractMutationResult> {
  return apiFetch(`${BASE(sessionId)}/field-contracts/${encodeURIComponent(contractId)}/activate`, {
    method: 'POST',
    ownerToken,
    label: 'FieldContracts',
  });
}

/** POST retire —— 409 retire_race。 */
export function retireContract(
  sessionId: string,
  contractId: string,
  ownerToken?: string | null,
): Promise<FieldContractMutationResult> {
  return apiFetch(`${BASE(sessionId)}/field-contracts/${encodeURIComponent(contractId)}/retire`, {
    method: 'POST',
    ownerToken,
    label: 'FieldContracts',
  });
}

export interface FieldContractErrorView {
  code: string;
  message: string;
  detail?: unknown;
}

/**
 * 把 apiFetch 抛出的 ApiError 收敛为 typed 错误视图：后端
 * detail={code,message,detail}（field_contracts 路由统一信封）；
 * 统一 data 信封与裸 detail 字符串也兜底（不解析文案判断语义）。
 */
export function asFieldContractError(err: unknown): FieldContractErrorView {
  const anyErr = err as {
    body?: unknown;
    message?: string;
  } | null;
  const body = anyErr?.body as
    | { detail?: unknown; data?: unknown; code?: unknown; message?: unknown }
    | undefined;
  const envelope =
    (body && typeof body === 'object' && typeof body.detail === 'object' && body.detail !== null
      ? body.detail
      : null) ??
    (body && typeof body === 'object' && typeof body.data === 'object' && body.data !== null
      ? body.data
      : null) ??
    null;
  if (envelope && 'code' in envelope) {
    const e = envelope as { code?: unknown; message?: unknown; detail?: unknown };
    return {
      code: typeof e.code === 'string' ? e.code : 'unknown',
      message: typeof e.message === 'string' ? e.message : String(err),
      detail: e.detail,
    };
  }
  return { code: 'network_error', message: anyErr?.message ?? String(err) };
}
