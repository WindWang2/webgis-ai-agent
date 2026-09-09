/**
 * Collab 前端（V6）—— 协议解析 / store / WS 客户端行为测试。
 * §15 必测项（前端部分）：事件重复/乱序（revision 门控）、重连对账、
 * 参与者边界、远端 doc 采纳不回声提交、BroadcastChannel 向后兼容
 * （见 lib/workbench/collab.test.ts 的 V5 基线，仍全绿）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  parseEnvelope,
  parseLeaseInfo,
  parseOpEntry,
  parseParticipant,
  participantColor,
  type CollabEnvelope,
} from './protocol';
import {
  collabApplyPresenceAction,
  collabMarkStaleRefs,
  collabPushRemoteOp,
  collabSetConflict,
  getCollabState,
  resetCollabStoreForTests,
} from './store';
import { setCollabKnownRevision, bindCollabRefetch } from './client';
import { resetUndoForTests } from '@/lib/workbench/undo';
import { resetLiveState, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  markWorkbenchHydrated,
  notifyWorkbenchSessionChanged,
  stopWorkbenchPersistence,
} from '@/lib/workbench/persistence';
import { useHudStore } from '@/lib/store/useHudStore';

function envelope(partial: Partial<CollabEnvelope>): CollabEnvelope {
  return {
    v: 1,
    kind: 'op',
    sid: 'sess-x',
    seq: 1,
    ts: new Date().toISOString(),
    data: {},
    ...partial,
  };
}

describe('protocol 解析与边界', () => {
  it('parseEnvelope：非法/未知 kind/缺 seq 丢弃', () => {
    expect(parseEnvelope(null)).toBeNull();
    expect(parseEnvelope({ v: 2, kind: 'op', sid: 's', seq: 1 })).toBeNull();
    expect(parseEnvelope(envelope({ kind: 'bogus' as never }))).toBeNull();
    expect(parseEnvelope(envelope({ seq: Number.NaN }))).toBeNull();
    const ok = parseEnvelope(envelope({}));
    expect(ok?.sid).toBe('sess-x');
  });

  it('parseParticipant：白名单 + 上限裁剪 + 隐私最小集', () => {
    const p = parseParticipant({
      clientId: 'c1',
      label: 'x'.repeat(500),
      selectionIds: Array.from({ length: 100 }, (_, i) => `l${i}`),
      token: 'secret', // 非白名单字段丢弃
    })!;
    expect(p.clientId).toBe('c1');
    expect(p.label!.length).toBe(120);
    expect(p.selectionIds).toHaveLength(50);
    expect((p as unknown as Record<string, unknown>).token).toBeUndefined();
    expect(parseParticipant({})).toBeNull();
  });

  it('parseLeaseInfo / parseOpEntry / participantColor', () => {
    expect(parseLeaseInfo({ lockKey: 'layer:l1', client: 'c1' })).toEqual({
      lockKey: 'layer:l1',
      client: 'c1',
      label: undefined,
    });
    expect(parseLeaseInfo({ client: 'c1' })).toBeNull();
    const op = parseOpEntry(envelope({ seq: 3, data: { kind: 'k', label: 'l', actor: 'user' } }), 3)!;
    expect(op.seq).toBe(3);
    expect(participantColor('c1')).toBe(participantColor('c1'));
  });
});

describe('collab store', () => {
  beforeEach(() => {
    resetCollabStoreForTests();
  });

  it('presence join/update/leave 维护参与者（含本端，UI 用 clientId 标注）', () => {
    collabApplyPresenceAction('join', { client: { clientId: 'a', label: 'A' } });
    collabApplyPresenceAction('join', { client: { clientId: 'b', label: 'B' } });
    expect(getCollabState().participants).toHaveLength(2);
    collabApplyPresenceAction('update', { client: { clientId: 'a', editingLayerId: 'l1' } });
    expect(getCollabState().participants.find((p) => p.clientId === 'a')?.editingLayerId).toBe('l1');
    collabApplyPresenceAction('leave', { client: { clientId: 'b' } });
    expect(getCollabState().participants).toHaveLength(1);
  });

  it('远端 journal 有界（200）', () => {
    for (let i = 0; i < 210; i += 1) {
      collabPushRemoteOp({
        id: `rop-${i}`, seq: i, actor: 'u', origin: 'user',
        kind: 'k', label: 'l', ts: i,
      });
    }
    expect(getCollabState().remoteOps).toHaveLength(200);
    expect(getCollabState().remoteOps[0].seq).toBe(10); // 旧的被挤出
  });

  it('stale refs 与冲突态', () => {
    collabMarkStaleRefs(['ref:a', 'ref:b'], true);
    expect(getCollabState().staleRefIds.has('ref:a')).toBe(true);
    collabMarkStaleRefs(['ref:a'], false);
    expect(getCollabState().staleRefIds.has('ref:a')).toBe(false);
    collabSetConflict('conflict!');
    expect(getCollabState().conflict?.message).toBe('conflict!');
    collabSetConflict(null);
    expect(getCollabState().conflict).toBeNull();
  });
});

/* ─── client + adopt 集成（mock WebSocket）───────────────────────────── */

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  constructor(public url: string, public protocols?: string[]) {
    MockWebSocket.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = 3;
  }
  // 测试驱动
  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(obj: unknown): void {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

const { startWorkbenchCollabV6, stopWorkbenchCollabV6 } = await import('./adopt');

describe('collab client + adopt（mock WS）', () => {
  let reconcileCalls: number;

  beforeEach(() => {
    vi.resetModules();
    MockWebSocket.instances = [];
    reconcileCalls = 0;
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    resetLiveState();
    resetCollabStoreForTests();
    resetUndoForTests();
    stopWorkbenchPersistence();
    setMapSpecSessionCursor('sess-x', 5, 'owner-tok');
    useHudStore.setState({ layerGroups: [], layerGroupMembership: {}, lockedLayerIds: [] });
    // 生产语义：远端水合只在「恢复完成（armed）」后受理（R1-m5 守卫）。
    notifyWorkbenchSessionChanged('sess-x');
    markWorkbenchHydrated();
    bindCollabRefetch(async () => {
      reconcileCalls += 1;
    });
    startWorkbenchCollabV6('sess-x');
    // start 会绑定真实 reconcile；测试在其后绑定计数桩（后绑定者生效）。
    bindCollabRefetch(async () => {
      reconcileCalls += 1;
    });
  });

  afterEach(() => {
    stopWorkbenchCollabV6();
    vi.unstubAllGlobals();
  });

  function lastSocket(): MockWebSocket {
    expect(MockWebSocket.instances.length).toBeGreaterThan(0);
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  it('认证：匿名会话以 session subprotocol 携带 owner token', () => {
    const ws = lastSocket();
    expect(ws.protocols?.[0]).toBe('session');
    expect(ws.protocols?.[1]).toBe('owner-tok');
  });

  it('hello：server doc 落地为已提交基线（不再回声提交）', async () => {
    const ws = lastSocket();
    const commitSpy = vi.spyOn(await import('@/lib/mapspec/user-mutation'), 'commitMapSpecMutation');
    ws.open();
    ws.receive({
      event: 'hello',
      data: {
        clientId: 'me-1', revision: 6, degraded: false,
        participants: [{ clientId: 'me-1', label: '访客' }],
        leases: [],
        doc: {
          version: 5,
          groups: [{ id: 'g1', name: '远端组', collapsed: false, parentId: null }],
          membership: {}, lockedLayerIds: [], mode: 'explore',
        },
      },
    });
    await new Promise((r) => setTimeout(r, 0));
    // 远端 doc 水合进 store
    expect(useHudStore.getState().layerGroups).toHaveLength(1);
    // 且不触发任何回声提交（远端真相 = 已提交）
    expect(commitSpy).not.toHaveBeenCalled();
    expect(getCollabState().participants[0].clientId).toBe('me-1');
    expect(commitSpy.mock.calls).toHaveLength(0);
  });

  it('revision 门控：更旧的 doc 事件被忽略（事件乱序/重复安全）', async () => {
    const ws = lastSocket();
    ws.open();
    ws.receive({
      event: 'hello',
      data: {
        clientId: 'me-1', revision: 8, degraded: false, participants: [], leases: [],
        doc: { version: 5, groups: [{ id: 'new', name: '新', collapsed: false, parentId: null }], membership: {}, lockedLayerIds: [], mode: 'explore', _rev: 8 },
      },
    });
    // 迟到的旧事件（seq/revision=6 < 8）→ 忽略
    ws.receive({
      v: 1, kind: 'doc', sid: 'sess-x', seq: 6, ts: '',
      data: { revision: 6, doc: { version: 5, groups: [{ id: 'old', name: '旧', collapsed: false, parentId: null }], membership: {}, lockedLayerIds: [], mode: 'explore', _rev: 6 } },
    });
    await new Promise((r) => setTimeout(r, 0));
    const groups = useHudStore.getState().layerGroups;
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe('new');
  });

  it('pong revision mismatch → 对账 refetch（重连补事件路径）', async () => {
    const ws = lastSocket();
    ws.open();
    ws.receive({ event: 'sync_ok', data: { revision: 6 } }); // 本地游标 5 → 对齐 6
    setCollabKnownRevision(6);
    ws.receive({ event: 'pong', data: { revision: 9 } }); // 服务端领先 → refetch
    await new Promise((r) => setTimeout(r, 0));
    expect(reconcileCalls).toBeGreaterThanOrEqual(1);
  });

  it('他端 presence join → 参与者出现；跨会话事件不串道', async () => {
    const ws = lastSocket();
    ws.open();
    ws.receive({
      v: 1, kind: 'presence', sid: 'sess-x', seq: 1, ts: '',
      data: { action: 'join', client: { clientId: 'peer-1', label: 'P1' } },
    });
    ws.receive({
      v: 1, kind: 'presence', sid: 'sess-other', seq: 2, ts: '',
      data: { action: 'join', client: { clientId: 'stranger', label: 'X' } },
    });
    await new Promise((r) => setTimeout(r, 0));
    const ids = getCollabState().participants.map((p) => p.clientId);
    expect(ids).toContain('peer-1');
    expect(ids).not.toContain('stranger');
  });
});
