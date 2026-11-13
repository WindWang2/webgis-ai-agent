/**
 * Feature-proposals store（G32）—— 提案队列/选中/加载态的治理投影。
 * useSyncExternalStore 订阅；权威在服务端 ProposalStore。
 */
import type { ProposalSummary } from '@/lib/api/feature-proposals';

export interface FeatureProposalsState {
  sessionId: string | null;
  proposals: ProposalSummary[];
  selectedId: string | null;
  status: 'idle' | 'loading' | 'ready' | 'error';
}

let state: FeatureProposalsState = {
  sessionId: null,
  proposals: [],
  selectedId: null,
  status: 'idle',
};

const listeners = new Set<() => void>();

function emit(): void {
  for (const fn of listeners) fn();
}

export function subscribeFeatureProposals(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getFeatureProposalsSnapshot(): FeatureProposalsState {
  return state;
}

export function getFeatureProposalsState(): FeatureProposalsState {
  return state;
}

export function proposalsSetSession(sessionId: string | null): void {
  if (state.sessionId === sessionId) return;
  state = { sessionId, proposals: [], selectedId: null, status: 'idle' };
  emit();
}

export function proposalsSetList(sessionId: string, proposals: ProposalSummary[]): void {
  state = { ...state, sessionId, proposals, status: 'ready' };
  emit();
}

export function proposalsSetStatus(status: FeatureProposalsState['status']): void {
  state = { ...state, status };
  emit();
}

export function proposalsSetSelected(selectedId: string | null): void {
  if (state.selectedId === selectedId) return;
  state = { ...state, selectedId };
  emit();
}

/** 测试重置钩子（vitest beforeEach）。 */
export function resetFeatureProposalsForTests(): void {
  state = { sessionId: null, proposals: [], selectedId: null, status: 'idle' };
  emit();
}
