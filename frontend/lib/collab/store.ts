/**
 * Collab 状态 store（Workbench V6）—— 连接态 / 参与者 / 远端 journal /
 * 冲突与降级披露。独立可订阅 store（useSyncExternalStore），不进 zustand
 * HUD store：这些是**通道状态**不是地图真相（与 collab.ts 的分层一致）。
 */
import type {
  CollabOpEntry,
  CollabParticipant,
  CollabLeaseInfo,
} from './protocol';
import { parseParticipant, COLLAB_MAX_REMOTE_OPS } from './protocol';

export type CollabStatus = 'offline' | 'connecting' | 'online' | 'degraded';

export interface CollabState {
  status: CollabStatus;
  /** 服务端分配的本端 clientId（offline 时为 null）。 */
  clientId: string | null;
  /** 参与者快照（不含本端 —— UI 另行标注「你」）。 */
  participants: CollabParticipant[];
  /** 当前会话的编辑租约（服务端快照）。 */
  leases: CollabLeaseInfo[];
  /** 远端操作 journal（跨浏览器；有界 200）。 */
  remoteOps: CollabOpEntry[];
  /** 降级（无总线）：正确性不受影响，UI 显式披露。 */
  degraded: boolean;
  /** 最近一次显式冲突（单次 rebase 失败后置位；用户确认后清除）。 */
  conflict: { at: number; message: string } | null;
  /** stale artifact ref 集合（artifact-status 投影 + artifact 事件合并）。 */
  staleRefIds: Set<string>;
}

interface CollabStoreInternal extends CollabState {
  version: number;
  listeners: Set<() => void>;
}

const store: CollabStoreInternal = {
  status: 'offline',
  clientId: null,
  participants: [],
  leases: [],
  remoteOps: [],
  degraded: false,
  conflict: null,
  staleRefIds: new Set<string>(),
  version: 0,
  listeners: new Set<() => void>(),
};

function emit(): void {
  store.version += 1;
  for (const fn of store.listeners) fn();
}

function patch(next: Partial<CollabState>): void {
  let changed = false;
  for (const [key, value] of Object.entries(next)) {
    if ((store as unknown as Record<string, unknown>)[key] !== value) {
      (store as unknown as Record<string, unknown>)[key] = value;
      changed = true;
    }
  }
  if (changed) emit();
}

export function subscribeCollab(listener: () => void): () => void {
  store.listeners.add(listener);
  return () => {
    store.listeners.delete(listener);
  };
}

export function getCollabSnapshot(): number {
  return store.version;
}

/** useSyncExternalStore 读取当前态（引用稳定：仅 emit 时 version 变化）。 */
export function getCollabState(): CollabState {
  return store;
}

/* ─── 通道写入面（client / adopt 调用；UI 只读）──────────────────────── */

export function collabSetStatus(status: CollabStatus, degraded = false): void {
  patch({ status, degraded, ...(status === 'online' ? {} : { clientId: null }) });
}

export function collabSetSessionIdentity(clientId: string | null, degraded: boolean): void {
  patch({ clientId, degraded });
}

export function collabSetParticipants(participants: CollabParticipant[]): void {
  patch({ participants: participants.slice(0, 32) });
}

export function collabApplyPresenceAction(action: string, raw: unknown): void {
  const participant = parseParticipant(
    (raw as Record<string, unknown> | undefined)?.client ?? raw,
  );
  if (action === 'leave') {
    if (participant == null) return;
    patch({
      participants: store.participants.filter((p) => p.clientId !== participant.clientId),
    });
    return;
  }
  if (participant == null) return;
  // 参与者列表含本端（hello 快照语义一致）；UI 用 clientId 标注「你」。
  const others = store.participants.filter((p) => p.clientId !== participant.clientId);
  if (action === 'join' || action === 'update') {
    patch({ participants: [...others, participant].slice(0, 32) });
  }
}

export function collabSetLeases(leases: CollabLeaseInfo[]): void {
  patch({ leases: leases.slice(0, 64) });
}

export function collabPushRemoteOp(entry: CollabOpEntry): void {
  const remoteOps = [...store.remoteOps, entry];
  if (remoteOps.length > COLLAB_MAX_REMOTE_OPS) {
    remoteOps.splice(0, remoteOps.length - COLLAB_MAX_REMOTE_OPS);
  }
  patch({ remoteOps });
}

export function collabMarkStaleRefs(ids: string[], invalidate: boolean): void {
  const next = new Set(store.staleRefIds);
  for (const id of ids) {
    if (invalidate) next.add(id);
    else next.delete(id);
  }
  patch({ staleRefIds: next });
}

export function collabSetConflict(message: string | null): void {
  patch({ conflict: message == null ? null : { at: Date.now(), message } });
}

export function collabResetForSession(): void {
  patch({
    status: 'offline',
    clientId: null,
    participants: [],
    leases: [],
    remoteOps: [],
    degraded: false,
    conflict: null,
    staleRefIds: new Set<string>(),
  });
}

/** 测试隔离。 */
export function resetCollabStoreForTests(): void {
  collabResetForSession();
  store.version = 0;
}
