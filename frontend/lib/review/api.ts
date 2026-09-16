/**
 * Review API client（ADR-0201）—— /chat/sessions/{sid}/review/* 的类型化
 * 薄封装。ownerToken 经 mapspec session cursor（与 collab adopt 同源）；
 * 401 刷新重试由 apiFetch 统一处理。
 */
import { apiFetch } from '@/lib/api/transport';
import type { ApiFetchOptions } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import type {
  ReviewMergeOutcome,
  ReviewProposalDetail,
} from './store';

function session(): { sessionId: string; ownerToken: string | null } | null {
  const { sessionId, ownerToken } = getMapSpecSessionCursor();
  if (sessionId == null) return null;
  return { sessionId, ownerToken: ownerToken ?? null };
}

function base(sessionId: string): string {
  return `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/review`;
}

function opts(
  ownerToken: string | null,
  init?: Partial<ApiFetchOptions>,
): ApiFetchOptions {
  return { ...init, ownerToken };
}

export async function listReviewProposals(): Promise<unknown[]> {
  const s = session();
  if (s == null) return [];
  const data = await apiFetch<{ proposals?: unknown[] }>(
    `${base(s.sessionId)}/proposals`,
    opts(s.ownerToken),
  );
  return data.proposals ?? [];
}

export async function getReviewProposalDetail(
  proposalId: string,
): Promise<ReviewProposalDetail> {
  const s = session();
  if (s == null) throw new Error('review: no session');
  return apiFetch<ReviewProposalDetail>(
    `${base(s.sessionId)}/proposals/${encodeURIComponent(proposalId)}`,
    opts(s.ownerToken),
  );
}

export async function createReviewProposal(input: {
  title: string;
  description?: string;
  mutation_intents: unknown[];
}): Promise<ReviewProposalDetail> {
  const s = session();
  if (s == null) throw new Error('review: no session');
  return apiFetch<ReviewProposalDetail>(
    `${base(s.sessionId)}/proposals`,
    opts(s.ownerToken, {
      method: 'POST',
      body: input,
    }),
  );
}

type ActionKind = 'submit' | 'comments' | 'decisions' | 'merge' | 'rebase'
  | 'withdraw' | 'supersede';

export async function reviewAction(
  proposalId: string,
  action: ActionKind,
  body?: Record<string, unknown>,
): Promise<ReviewProposalDetail> {
  const s = session();
  if (s == null) throw new Error('review: no session');
  const detail = await apiFetch<ReviewProposalDetail>(
    `${base(s.sessionId)}/proposals/${encodeURIComponent(proposalId)}/${action}`,
    opts(s.ownerToken, {
      method: 'POST',
      body: body ?? {},
    }),
  );
  return detail;
}

export function extractMergeOutcome(detail: ReviewProposalDetail): ReviewMergeOutcome | null {
  return detail.merge_outcome ?? null;
}

export async function exportReviewRecords(): Promise<unknown> {
  const s = session();
  if (s == null) throw new Error('review: no session');
  return apiFetch<unknown>(`${base(s.sessionId)}/export`, opts(s.ownerToken));
}
