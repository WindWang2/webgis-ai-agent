/**
 * G43（本轮实际上下文回执与可控资料）：类型与 API client。
 *
 * 事实纪律（ADR-0295）：
 * - 「本轮提交给 Pi 的资料」= 宿主本轮新装配的 prompt 块（有 receipt 证据），
 *   ≠ 模型完整读过的全部上下文；queued ≠ 已消费、handled ≠ 已运行；
 * - 排除只影响之后的新装配，绝不声称历史「已遗忘」；
 * - tokens 是渲染估算，不是供应商计费。
 */

export type TurnReceiptStatus = 'assembled' | 'not_sent' | 'submitted' | 'unknown';
export type PiDisposition = 'started' | 'queued' | 'handled';
export type TurnOutcome = 'completed' | 'failed' | 'cancelled' | null;

/** SSE turn_context_receipt 事件 / messages.context_receipt 的有界摘要。 */
export interface TurnContextReceiptSummary {
  session_id: string;
  turn_id: string;
  receipt_id: number | null;
  digest: string | null;
  status: TurnReceiptStatus | string;
  disposition: PiDisposition | string | null;
  turn_outcome: TurnOutcome | string;
  policy_version: number;
  mode: string;
  counts: {
    included?: number;
    omitted?: number;
    truncated?: number;
    sources_total?: number;
  };
  persisted: boolean;
  not_sent_reason?: string | null;
}

/** 单条可选资料源在回执中的去向。 */
export interface ReceiptSourceLine {
  source_id: string;
  kind: string;
  decision: string;
  item_id?: string;
  version_token?: string;
  label?: string;
}

/** GET context-receipts/{turn_id} 的完整读模型。 */
export interface TurnReceiptDetail extends TurnContextReceiptSummary {
  assembly_mode: string;
  run_id: string;
  created_at: string | null;
  updated_at: string | null;
  payload: {
    items?: Array<Record<string, unknown>>;
    sources?: ReceiptSourceLine[];
    sources_total?: number;
    planned_tokens?: number;
    actual_tokens?: number;
    usable_tokens?: number;
    over_budget?: boolean;
    [key: string]: unknown;
  };
}

/** 可选资料目录条目。 */
export interface OptionalSourceView {
  source_id: string;
  kind: 'project_knowledge_entry' | 'carto_memory_fact' | 'gis_memory_block' | string;
  label: string;
  version_token: string;
  entity_kind?: string;
  authority?: string;
  note?: string;
  selectable: boolean;
}

/** 会话策略（GET/PUT context-policy）。 */
export interface SessionSourcePolicyView {
  session_id: string;
  policy: {
    mode: 'auto' | 'explicit' | string;
    preferred: string[];
    excluded: string[];
    policy_version: number;
  };
  scope_note?: string;
  effective_when?: string;
}

/** 无副作用预览（POST context-preview）。 */
export interface TurnContextPreview {
  session_id: string;
  generated_at: string;
  is_preview: boolean;
  disclaimer: string;
  policy: SessionSourcePolicyView['policy'] | null;
  blocks: Array<{
    domain: string;
    available: boolean;
    reason: string;
    est_tokens: number;
    chars?: number;
  }>;
  sources: ReceiptSourceLine[];
  estimate: {
    optional_priors_tokens: number;
    message_draft_tokens: number;
    note: string;
  };
  not_previewed: string[];
}

import { apiFetch, ApiError } from '@/lib/api/transport';

export async function fetchOptionalSources(
  sessionId: string,
  projectId: string,
  ownerToken?: string | null,
): Promise<{ sources: OptionalSourceView[]; counts: Record<string, number> }> {
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  const qs = params.toString();
  return apiFetch<{ sources: OptionalSourceView[]; counts: Record<string, number> }>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/context-sources${qs ? `?${qs}` : ''}`,
    { label: 'turn context sources load', ownerToken },
  );
}

export async function fetchSourcePolicy(
  sessionId: string,
  ownerToken?: string | null,
): Promise<SessionSourcePolicyView> {
  return apiFetch<SessionSourcePolicyView>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/context-policy`,
    { label: 'turn context policy load', ownerToken },
  );
}

export class PolicyConflictError extends Error {
  current: SessionSourcePolicyView['policy'];
  constructor(current: SessionSourcePolicyView['policy']) {
    super('policy_version conflict');
    this.current = current;
  }
}

export async function putSourcePolicy(
  sessionId: string,
  body: {
    mode: string;
    preferred: string[];
    excluded: string[];
    expected_version: number;
    project_id: string;
  },
  ownerToken?: string | null,
): Promise<SessionSourcePolicyView> {
  try {
    return await apiFetch<SessionSourcePolicyView>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/context-policy`,
      {
        method: 'PUT',
        body,
        label: 'turn context policy put',
        ownerToken,
      },
    );
  } catch (err) {
    // 409 冲突：携带当前策略供 UI 合并提示（C12）。统一错误信封把结构化
    // detail 放在 body.data（旧体 legacy detail 仅在显式回退头下出现）。
    if (err instanceof ApiError && err.status === 409) {
      const body = err.body as {
        data?: { current?: SessionSourcePolicyView['policy'] };
        detail?: { current?: SessionSourcePolicyView['policy'] };
      } | undefined;
      const current = body?.data?.current ?? body?.detail?.current;
      if (current) {
        throw new PolicyConflictError(current);
      }
    }
    throw err;
  }
}

export async function fetchTurnReceipt(
  sessionId: string,
  turnId: string,
  ownerToken?: string | null,
): Promise<TurnReceiptDetail | null> {
  try {
    return await apiFetch<TurnReceiptDetail>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/context-receipts/${encodeURIComponent(turnId)}`,
      { label: 'turn receipt load', ownerToken },
    );
  } catch {
    return null; // 404/legacy → coverage limited（不伪造）
  }
}

export async function postContextPreview(
  sessionId: string,
  body: { project_id: string; message_draft?: string; policy_draft?: unknown },
  ownerToken?: string | null,
): Promise<TurnContextPreview> {
  return apiFetch<TurnContextPreview>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/context-preview`,
    {
      method: 'POST',
      body,
      label: 'turn context preview',
      ownerToken,
    },
  );
}
