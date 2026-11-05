/**
 * Situation 投影前端消费面（ADR-0283 D5）—— 刷新/重连的状态面恢复数据源。
 *
 * - 模块单例 store（useSyncExternalStore 订阅，不进 zustand —— 与
 *   selection-store/undo 同款纪律）；
 * - `refreshSituationProjection(sessionId)` 定向拉取两个只读端点
 *   （/situation + /turn-chain），失败 → status=error（诚实，不伪造空态）；
 * - **恢复语义**：boot/重连后首次成功拉取且本地 selection 为空时，把服务端
 *   claimed_scope 重放为 selection filter（服务端事实源，localStorage 非权威）；
 * - 不写任何权威状态；不驱动 turn。
 */
import { useSyncExternalStore } from 'react';
import { apiFetch } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { getSelection, publishSelection } from '@/lib/selection/selection-store';

export interface ClaimedScope {
  kind: 'feature_ids' | 'feature_single' | 'aoi' | 'none';
  layer_id?: string | null;
  dataset_ref?: string | null;
  feature_ids?: string[] | null;
  bbox?: number[] | null;
  claimed_at_seq?: number | null;
}

export interface TurnChainChange {
  class: string;
  operation_id?: string;
  origin?: string;
  status?: string;
  kind?: string;
  layer_id?: string | null;
  wins?: boolean;
  effects?: Array<{ kind: string; targets: string[]; state: string }>;
}

export interface TurnChainView {
  turns: Array<{
    turn_id: string;
    intent_digest?: string | null;
    revision_span?: [number, number] | null;
    changes: TurnChainChange[];
  }>;
  observed_revision?: number | null;
  degraded?: string[];
  counts: {
    user_explicit: number;
    agent_receipts: number;
    agent_pending: number;
    executed_observed: number;
  };
}

export interface SituationView {
  schema_version: string;
  session_id: string;
  situation: {
    identity?: { session_id?: string; turn_id?: string };
    interaction?: {
      selected_feature?: { value?: Record<string, unknown>; status?: string };
      pending_mutations?: { value?: Array<Record<string, unknown>>; status?: string };
      user_hidden_layers?: { value?: string[]; status?: string };
    };
    map?: {
      desired_revision?: { value?: number };
      observed?: { value?: unknown; status?: string };
    };
  };
  inconsistencies: Array<{ code: string; severity: string }>;
  interaction_snapshot: {
    viewport?: Record<string, unknown> | null;
    selection?: Record<string, unknown> | null;
    chart_selection?: Record<string, unknown> | null;
    temporal_window?: Record<string, unknown> | null;
    attempted_not_applied?: Array<Record<string, unknown>>;
  };
  selection_history: Array<{
    epoch_seq: number;
    layer_id?: string | null;
    feature_ids: string[];
    superseded: boolean;
    cleared: boolean;
  }>;
  claimed_scope: ClaimedScope;
}

export type SituationProjectionStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface SituationProjectionState {
  status: SituationProjectionStatus;
  sessionId: string | null;
  view: SituationView | null;
  turnChain: TurnChainView | null;
  fetchedAtSeq: number;
  error?: string;
}

let state: SituationProjectionState = {
  status: 'idle',
  sessionId: null,
  view: null,
  turnChain: null,
  fetchedAtSeq: 0,
};

const listeners = new Set<() => void>();
let version = 0;

function emitChange(): void {
  version += 1;
  for (const fn of listeners) fn();
}

export function subscribeSituationProjection(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getSituationProjection(): SituationProjectionState {
  return state;
}

export function getSituationProjectionVersion(): number {
  return version;
}

/** 会话切换/测试复位。 */
export function resetSituationProjection(): void {
  state = {
    status: 'idle',
    sessionId: null,
    view: null,
    turnChain: null,
    fetchedAtSeq: 0,
  };
  emitChange();
}

/** boot 重放标记（有界：>32 会话时最旧的让位——防长生命周期无界）。 */
let replayedSessions = new Set<string>();
function markReplayed(sid: string): void {
  if (replayedSessions.size >= 32) {
    const oldest = replayedSessions.values().next().value;
    if (oldest) replayedSessions.delete(oldest);
  }
  replayedSessions.add(sid);
}

/**
 * 拉取服务端 situation/turn-chain 投影（只读）。成功后若本会话尚未做过
 * boot 重放且本地无选择 → 把 claimed_scope 重放为 selection filter。
 */
export async function refreshSituationProjection(
  sessionId?: string,
  options: { replaySelection?: boolean } = {},
): Promise<void> {
  const sid = sessionId ?? getMapSpecSessionCursor().sessionId ?? undefined;
  if (!sid) return;
  if (state.sessionId !== sid) {
    state = { ...state, status: 'loading', sessionId: sid, view: null, turnChain: null };
    emitChange();
  } else if (state.status !== 'ready') {
    state = { ...state, status: 'loading' };
    emitChange();
  }
  try {
    const ownerToken = getMapSpecSessionCursor().ownerToken ?? null;
    const view = await apiFetch<SituationView>(
      `/api/v1/chat/sessions/${sid}/situation`,
      { ownerToken, timeoutMs: 15_000, label: 'Situation projection' },
    );
    let turnChain: TurnChainView | null = null;
    try {
      turnChain = await apiFetch<TurnChainView>(
        `/api/v1/chat/sessions/${sid}/turn-chain`,
        { ownerToken, timeoutMs: 15_000, label: 'Turn chain' },
      );
    } catch {
      turnChain = null; // 链面缺席诚实为空（状态面仍可用）
    }
    state = {
      status: 'ready',
      sessionId: sid,
      view,
      turnChain,
      fetchedAtSeq: state.fetchedAtSeq + 1,
    };
    emitChange();
    if (options.replaySelection !== false && !replayedSessions.has(sid)) {
      markReplayed(sid);
      replaySelectionFromScope(view);
    }
  } catch (err) {
    state = {
      ...state,
      status: 'error',
      error: err instanceof Error ? err.message : String(err),
      fetchedAtSeq: state.fetchedAtSeq + 1,
    };
    emitChange();
  }
}

/**
 * 服务端 claimed_scope → 本地 selection filter 重放（仅当本地无选择——
 * 用户当前操作优先，服务端事实只补空白，绝不覆盖现场）。
 */
function replaySelectionFromScope(view: SituationView): void {
  try {
    if (getSelection() !== null) return;
    const scope = view.claimed_scope;
    if (!scope || scope.kind === 'none' || !scope.layer_id) return;
    if (scope.kind === 'feature_ids' && scope.feature_ids?.length) {
      publishSelection('select', {
        source: 'map',
        layer_id: scope.layer_id,
        selected_ids: scope.feature_ids.slice(0, 50),
      });
    } else if (scope.kind === 'feature_single') {
      const single = scope.feature_ids?.[0];
      if (single) {
        publishSelection('select', {
          source: 'map',
          layer_id: scope.layer_id,
          feature_id: single,
        });
      }
    }
    // AOI 面不重放画布几何（本体在客户端；状态面披露即可）。
  } catch {
    // 恢复是增值面，绝不阻塞。
  }
}

/** React 订阅面。 */
export function useSituationProjection(): SituationProjectionState {
  return useSyncExternalStore(
    subscribeSituationProjection,
    getSituationProjection,
    getSituationProjection,
  );
}
