/**
 * Review store（ADR-0201 空间审查/会签）—— proposal 列表 / 详情投影 /
 * 加载态。独立可订阅 store（useSyncExternalStore），与 collab store 同分层：
 * 审查状态是**治理投影**不是地图真相；authority 在服务端 ReviewStore。
 */
import type { CollabReviewEvent } from '@/lib/collab/protocol';

export type ReviewProposalStatus =
  | 'draft'
  | 'submitted'
  | 'changes_requested'
  | 'approved'
  | 'rejected'
  | 'merged'
  | 'superseded'
  | 'withdrawn';

export interface ReviewActorLite {
  actor_id: string;
  actor_kind: 'user' | 'agent';
  role: string;
}

export interface ReviewProposalSummary {
  proposal_id: string;
  title: string;
  status: ReviewProposalStatus;
  risk: 'low' | 'high';
  base_revision: number;
  author: ReviewActorLite;
  updated_at: string;
}

export interface ReviewMergeOutcome {
  ok: boolean;
  conflict: boolean;
  interleaved: boolean;
  rolled_back: boolean;
  failure: string | null;
}

export interface ReviewProposalDetail {
  proposal: Record<string, unknown> & { proposal_id: string; title: string };
  anchor_states: Record<string, string>;
  conflict: boolean;
  current_revision: number;
  base_revision: number;
  policy: {
    snapshot: Record<string, unknown>;
    satisfied: boolean;
    counted_approvals: number;
    blocking_reasons: string[];
  };
  merge_outcome?: ReviewMergeOutcome;
}

export type ReviewLoadStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface ReviewState {
  sessionId: string | null;
  status: ReviewLoadStatus;
  proposals: ReviewProposalSummary[];
  selectedId: string | null;
  detail: ReviewProposalDetail | null;
  /** 最近一次 review 总线事件的回声（drawer 闪烁提示用；有界 1 条）。 */
  lastEventAt: number | null;
}

interface ReviewStoreInternal extends ReviewState {
  version: number;
  listeners: Set<() => void>;
}

const store: ReviewStoreInternal = {
  sessionId: null,
  status: 'idle',
  proposals: [],
  selectedId: null,
  detail: null,
  lastEventAt: null,
  version: 0,
  listeners: new Set<() => void>(),
};

function emit(): void {
  store.version += 1;
  for (const fn of store.listeners) fn();
}

function patch(next: Partial<ReviewState>): void {
  let changed = false;
  for (const [key, value] of Object.entries(next)) {
    if ((store as unknown as Record<string, unknown>)[key] !== value) {
      (store as unknown as Record<string, unknown>)[key] = value;
      changed = true;
    }
  }
  if (changed) emit();
}

export function subscribeReview(listener: () => void): () => void {
  store.listeners.add(listener);
  return () => {
    store.listeners.delete(listener);
  };
}

export function getReviewSnapshot(): number {
  return store.version;
}

export function getReviewState(): ReviewState {
  return store;
}

/* ─── 写入面（api client / adopt 调用；UI 只读）─────────────────────── */

const MAX_LIST = 100;

function asSummary(raw: unknown): ReviewProposalSummary | null {
  if (raw == null || typeof raw !== 'object') return null;
  const c = raw as Record<string, unknown>;
  if (typeof c.proposal_id !== 'string' || typeof c.title !== 'string') return null;
  const authorRaw = (c.author ?? {}) as Record<string, unknown>;
  return {
    proposal_id: c.proposal_id,
    title: c.title.slice(0, 200),
    status: (typeof c.status === 'string' ? c.status : 'draft') as ReviewProposalStatus,
    risk: c.risk === 'high' ? 'high' : 'low',
    base_revision: typeof c.base_revision === 'number' ? c.base_revision : 0,
    author: {
      actor_id: typeof authorRaw.actor_id === 'string' ? authorRaw.actor_id : 'unknown',
      actor_kind: authorRaw.actor_kind === 'agent' ? 'agent' : 'user',
      role: typeof authorRaw.role === 'string' ? authorRaw.role : 'viewer',
    },
    updated_at: typeof c.updated_at === 'string' ? c.updated_at : '',
  };
}

export function reviewSetSession(sessionId: string | null): void {
  patch({ sessionId, proposals: [], selectedId: null, detail: null, status: sessionId == null ? 'idle' : store.status });
}

export function reviewSetProposals(sessionId: string, items: unknown[]): void {
  const proposals = items
    .map(asSummary)
    .filter((x): x is ReviewProposalSummary => x != null)
    .slice(0, MAX_LIST);
  patch({ sessionId, proposals, status: 'ready' });
}

export function reviewSetStatus(status: ReviewLoadStatus): void {
  patch({ status });
}

export function reviewSetSelected(proposalId: string | null): void {
  patch({ selectedId: proposalId, detail: null });
}

export function reviewSetDetail(detail: ReviewProposalDetail | null): void {
  patch({ detail });
}

export function reviewSetMergeOutcome(outcome: ReviewMergeOutcome): void {
  if (store.detail == null) return;
  patch({ detail: { ...store.detail, merge_outcome: outcome } });
}

/** adopt 落地：review 总线事件 → 标记事件回声（触发 UI refetch 信号）。 */
export function reviewApplyBusEvent(_event: CollabReviewEvent): void {
  patch({ lastEventAt: Date.now() });
}

export function reviewResetForSession(): void {
  patch({
    sessionId: null,
    status: 'idle',
    proposals: [],
    selectedId: null,
    detail: null,
    lastEventAt: null,
  });
}

/** 测试隔离。 */
export function resetReviewStoreForTests(): void {
  reviewResetForSession();
  store.version = 0;
}
