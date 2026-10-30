/**
 * Map Operation / Display Confirmation 客户端（ADR-0278）。
 *
 * 全部走 require_owned_session 端点 —— apiFetch 的 ownerToken/Bearer
 * 头与 visual-repairs 同族。服务端对跨会话/未认证 fail-closed（404/401），
 * 客户端只需如实呈现错误，不做本地兜底成功。
 */
import { apiFetch } from './transport';
import type { MapOperationReceiptView } from '@/lib/map-operations/receipt-bus';

export interface DisplayConfirmationStatus {
  session_id: string;
  mode: 'auto' | 'required' | string;
  confirmed: boolean;
  pending: boolean;
  required_render_seq: number;
  latest_observation_seq: number;
  verdict_present: boolean;
}

export interface DisplayAckResponse {
  accepted: boolean;
  confirmed: boolean;
  render_seq?: number;
  reason?: string | null;
  task_complete?: boolean | null;
}

function _base(sessionId: string): string {
  return `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`;
}

export async function fetchDisplayConfirmation(
  sessionId: string,
  ownerToken?: string | null,
): Promise<DisplayConfirmationStatus> {
  return apiFetch<DisplayConfirmationStatus>(
    `${_base(sessionId)}/display-confirmation`,
    { ownerToken, label: 'Display confirmation status error' },
  );
}

export async function postDisplayAck(
  sessionId: string,
  renderSeq: number,
  ownerToken?: string | null,
): Promise<DisplayAckResponse> {
  return apiFetch<DisplayAckResponse>(
    `${_base(sessionId)}/display-confirmation/ack`,
    {
      method: 'POST',
      body: { render_seq: renderSeq },
      ownerToken,
      label: 'Display ack error',
    },
  );
}

export async function fetchMapOperations(
  sessionId: string,
  ownerToken?: string | null,
): Promise<MapOperationReceiptView[]> {
  const resp = await apiFetch<{ operations?: MapOperationReceiptView[] }>(
    `${_base(sessionId)}/map-operations`,
    { ownerToken, label: 'Map operations list error' },
  );
  return Array.isArray(resp?.operations) ? resp.operations : [];
}

export async function commitPendingMapOperation(
  sessionId: string,
  operationId: string,
  ownerToken?: string | null,
): Promise<MapOperationReceiptView> {
  const resp = await apiFetch<{ receipt?: MapOperationReceiptView }>(
    `${_base(sessionId)}/map-operations/${encodeURIComponent(operationId)}/commit`,
    {
      method: 'POST',
      body: {},
      ownerToken,
      label: 'Map operation commit error',
    },
  );
  return resp?.receipt ?? { operation_id: operationId, status: 'error' };
}
