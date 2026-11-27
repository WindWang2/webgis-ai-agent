/**
 * Review store（ADR-0203 空间审查/会签）—— proposal 列表 / 详情投影 /
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
  /**
   * 会话生命周期哨兵（P3 加固）：reviewSetSession 绑定或 reviewResetForSession
   * 重置后置位。置位期间若 store.sessionId 为 null（reset 之后、setSession 之前
   * 的空窗），迟到响应一律拒写 —— 否则旧会话的列表/详情会重新填充已重置的
   * store，造成跨会话数据串显。模块初始为 false；仅 resetReviewStoreForTests 复位。
   */
  lifecycleArmed: boolean;
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
  lifecycleArmed: false,
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

/**
 * 会话空窗判定（P3 加固）：reviewResetForSession 之后、reviewSetSession 之前，
 * store.sessionId 为 null —— 此窗口内到达的列表/详情响应，其请求必然发起于
 * 重置之前，属旧会话残留，拒写以防重新填充已重置的 store。
 * 从未进入过会话生命周期的 pristine store 不受约束（保持既有写入面）。
 */
function inSessionGap(): boolean {
  return store.sessionId == null && store.lifecycleArmed;
}

export function reviewSetSession(sessionId: string | null): void {
  store.lifecycleArmed = true;
  patch({ sessionId, proposals: [], selectedId: null, detail: null, status: sessionId == null ? 'idle' : store.status });
}

export function reviewSetProposals(sessionId: string, items: unknown[]): void {
  // stale 响应守卫（review R1-P3，与 reviewSetDetail 同姿势）：迟到旧会话的
  // 列表响应不得覆盖当前会话列表，也不得把 store.sessionId 回退到入参值。
  // 入参 sessionId 由调用方经 reviewSetSession 绑定，这里只读不写。
  if (store.sessionId != null && store.sessionId !== sessionId) return;
  // P3：会话空窗内（reset 后、setSession 前，store.sessionId 为 null）上面的
  // 守卫全部失效 —— 旧会话迟到列表会重新填充已重置的 store，这里补齐拒写。
  if (inSessionGap()) return;
  const proposals = items
    .map(asSummary)
    .filter((x): x is ReviewProposalSummary => x != null)
    .slice(0, MAX_LIST);
  patch({ proposals, status: 'ready' });
}

export function reviewSetStatus(status: ReviewLoadStatus): void {
  patch({ status });
}

export function reviewSetSelected(proposalId: string | null): void {
  patch({ selectedId: proposalId, detail: null });
}

export function reviewSetDetail(detail: ReviewProposalDetail | null): void {
  // stale 响应守卫（review R1-P3）：晚到的旧详情不得覆盖当前选中项。
  if (
    detail != null &&
    store.selectedId != null &&
    detail.proposal.proposal_id !== store.selectedId
  ) {
    return;
  }
  // P3：会话空窗内（reset 后、setSession 前）迟到详情同拒，不重填已重置 store。
  if (detail != null && inSessionGap()) return;
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
  store.lifecycleArmed = true;
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
  store.lifecycleArmed = false;
  store.version = 0;
}
