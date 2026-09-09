/**
 * Workbench 协作 UI（Workbench V6）—— 协作状态条。
 *
 * - 参与者头像排（presence；含本端标注「你」）；
 * - 通道状态 pill（online / degraded / offline / 冲突）—— 降级与冲突显式
 *   披露（§I：no silent no-op）；
 * - 编辑租约徽标（谁在编辑哪层/组）；
 * - 全部数据来自 collab store（通道状态，非地图真相）。
 */
import React, { useSyncExternalStore, useCallback } from 'react';
import clsx from 'clsx';
import { Users, Wifi, WifiOff, AlertTriangle, Pencil } from 'lucide-react';
import {
  subscribeCollab,
  getCollabState,
  collabSetConflict,
  type CollabState,
} from '@/lib/collab/store';
import { participantColor, COLLAB_MAX_PARTICIPANTS } from '@/lib/collab/protocol';
import { sendCollabLease } from '@/lib/collab/client';

export function useCollabState(): CollabState {
  return useSyncExternalStore(subscribeCollab, getCollabState, getCollabState);
}

function StatusPill({ status, degraded }: { status: CollabState['status']; degraded: boolean }) {
  if (status === 'online' && !degraded) {
    return (
      <span
        className="flex items-center gap-1 rounded-full bg-status-ok-soft px-1.5 py-0.5 text-micro text-ink-muted"
        title="服务端协作通道已连接（跨浏览器实时同步）"
      >
        <Wifi aria-hidden size={10} />
        协作
      </span>
    );
  }
  if (status === 'offline') {
    return (
      <span
        className="flex items-center gap-1 rounded-full bg-surface-sunken px-1.5 py-0.5 text-micro text-ink-disabled"
        title="未连接服务端协作通道 —— 单用户模式（组织态仍经服务端 CAS 持久化）"
      >
        <WifiOff aria-hidden size={10} />
        单用户
      </span>
    );
  }
  // degraded 或 connecting：降级显式披露（正确性不受影响 —— revision 对账兜底）
  const label = status === 'degraded' || degraded ? '降级同步' : '连接中';
  return (
    <span
      className="flex items-center gap-1 rounded-full bg-status-warn-soft px-1.5 py-0.5 text-micro text-ink"
      title="服务端事件通道降级/建立中：变更仍经服务端 CAS 收敛，其他浏览器更新以对账轮询送达"
    >
      <AlertTriangle aria-hidden size={10} />
      {label}
    </span>
  );
}

/** 参与者头像排（最多展示 4 + 计数溢出）。 */
function PresenceAvatars({ state }: { state: CollabState }) {
  const { participants, clientId } = state;
  if (participants.length === 0) return null;
  const shown = participants.slice(0, 4);
  const overflow = participants.length - shown.length;
  return (
    <div className="flex items-center -space-x-1">
      <Users aria-hidden size={12} className="mr-1 text-ink-disabled" />
      {shown.map((p) => {
        const color = participantColor(p.clientId);
        const isSelf = p.clientId === clientId;
        const editing = p.editingLayerId != null || p.editingGroupId != null;
        return (
          <span
            key={p.clientId}
            title={`${p.label ?? '参与者'}${isSelf ? '（你）' : ''}${p.editingLayerId ? ' · 编辑中' : ''}`}
            className={clsx(
              'flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-semibold text-white ring-1 ring-surface',
              editing && 'ring-2',
            )}
            style={{ backgroundColor: color, ...(editing ? { boxShadow: `0 0 0 1px ${color}` } : {}) }}
          >
            {(p.label ?? '?').slice(0, 1)}
          </span>
        );
      })}
      {overflow > 0 && (
        <span
          className="flex h-4 items-center rounded-full bg-surface-sunken px-1 text-[9px] text-ink-muted"
          title={`其他参与者 ${overflow} 人（上限 ${COLLAB_MAX_PARTICIPANTS}）`}
        >
          +{overflow}
        </span>
      )}
    </div>
  );
}

/** 编辑租约行（谁正在编辑什么 + 接管入口 = 请求同锁会被拒，披露即可）。 */
function LeaseStrip({ state }: { state: CollabState }) {
  const { leases, clientId } = state;
  const others = leases.filter((l) => l.client !== clientId).slice(0, 3);
  if (others.length === 0) return null;
  return (
    <div className="flex items-center gap-1 border-t border-edge-subtle px-panel py-0.5 text-micro text-ink-muted">
      <Pencil aria-hidden size={10} />
      {others.map((l) => (
        <span key={l.lockKey} className="truncate">
          {l.label ?? '他人'} 正在编辑 {l.lockKey.startsWith('group:') ? '分组' : '图层'}
        </span>
      ))}
    </div>
  );
}

/** 冲突横幅（单次 rebase 失败后的显式态；用户确认后清除）。 */
function ConflictBanner({ state }: { state: CollabState }) {
  const clear = useCallback(() => collabSetConflict(null), []);
  if (state.conflict == null) return null;
  return (
    <div
      role="alert"
      className="flex items-center gap-2 border-b border-edge-subtle bg-status-warn-soft px-panel py-1 text-micro text-ink"
    >
      <AlertTriangle aria-hidden size={12} />
      <span className="truncate">{state.conflict.message}</span>
      <button
        type="button"
        className="ml-auto shrink-0 rounded-xs px-1 py-0.5 hover:bg-surface-hover"
        onClick={clear}
      >
        知道了
      </button>
    </div>
  );
}

/**
 * 协作状态条（LayersTab 头部下方常驻；单用户离线态同样诚实披露）。
 */
export function CollabBar(): React.ReactElement | null {
  const state = useCollabState();
  const showPresence = state.status === 'online' || state.status === 'degraded';
  return (
    <div data-testid="collab-bar">
      <ConflictBanner state={state} />
      <div className="flex items-center gap-2 border-b border-edge-subtle px-panel py-1">
        <StatusPill status={state.status} degraded={state.degraded} />
        {showPresence && <PresenceAvatars state={state} />}
        {state.status === 'offline' && (
          <span className="text-micro text-ink-disabled">
            登录后可跨浏览器协作（组织态仍持久化）
          </span>
        )}
      </div>
      {showPresence && <LeaseStrip state={state} />}
    </div>
  );
}

/** 编辑租约操作（图层行「编辑」入口：acquire / 释放）。 */
export function useLayerLease(): {
  acquire: (layerId: string) => void;
  release: (layerId: string) => void;
} {
  const state = useCollabState();
  const clientId = state.clientId;
  const acquire = useCallback(
    (layerId: string) => {
      if (clientId == null) return;
      sendCollabLease('lease_acquire', `layer:${layerId}`);
    },
    [clientId],
  );
  const release = useCallback(
    (layerId: string) => {
      if (clientId == null) return;
      sendCollabLease('lease_release', `layer:${layerId}`);
    },
    [clientId],
  );
  return { acquire, release };
}
