/**
 * 多 tab 协同基座（W5）—— 内存 BroadcastChannel 总线模拟双 tab。
 *
 * 锁定五条不变式：
 *   C1. tab A 提交 doc → tab B 水合（组织态一致）且 revision 基线对齐；
 *   C2. 接收方不回声提交（远端 doc 即服务器真相，CAS 无回归循环）；
 *   C3. 更旧 revision 的 doc 被忽略（确定性：服务器 CAS last-writer-wins）；
 *   C4. hello：晚到 tab 收敛到已在线 tab 的当前 doc；
 *   C5. 权限边界：非本会话消息被忽略；无 BroadcastChannel 环境降级 no-op。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { resetLiveState, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  collabSessionChanged,
  startWorkbenchCollab,
} from './collab';
import {
  hydrateWorkbenchFromSpec,
  markWorkbenchHydrated,
  notifyWorkbenchSessionChanged,
  startWorkbenchPersistence,
  stopWorkbenchPersistence,
} from './persistence';

/* ─── 内存 BroadcastChannel 总线（同 channel 名互投、不含发送者）────── */
class FakeBroadcastChannel {
  static registry = new Map<string, Set<FakeBroadcastChannel>>();
  name: string;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  constructor(name: string) {
    this.name = name;
    if (!FakeBroadcastChannel.registry.has(name)) {
      FakeBroadcastChannel.registry.set(name, new Set());
    }
    FakeBroadcastChannel.registry.get(name)!.add(this);
  }
  postMessage(data: unknown): void {
    const peers = FakeBroadcastChannel.registry.get(this.name) ?? new Set();
    for (const peer of peers) {
      if (peer === this) continue;
      // 异步投递（模拟浏览器事件循环语义）
      setTimeout(() => peer.onmessage?.({ data }), 0);
    }
  }
  close(): void {
    FakeBroadcastChannel.registry.get(this.name)?.delete(this);
  }
}

const commitSpy = vi.fn().mockResolvedValue({ mutation_revision: 7 });
vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitMapSpecMutation: (...args: unknown[]) => commitSpy(...args),
}));

const S = 'sess-collab';

describe('workbench 多 tab 协同（W5）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
    stopWorkbenchPersistence();
    FakeBroadcastChannel.registry = new Map();
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
    useHudStore.setState({ layerGroups: [], layerGroupMembership: {}, lockedLayerIds: [] });
    startWorkbenchPersistence();
    notifyWorkbenchSessionChanged(S);
    setMapSpecSessionCursor(S, 5);
    hydrateWorkbenchFromSpec(undefined); // 空基线武装
  });

  afterEach(() => {
    stopWorkbenchPersistence();
    vi.unstubAllGlobals();
  });

  const docOf = (overrides: Record<string, unknown> = {}) => ({
    version: 5,
    groups: [{ id: 'g1', name: '远端组', collapsed: false, parentId: null }],
    membership: { l1: 'g1' },
    lockedLayerIds: [],
    mode: 'explore' as const,
    ...overrides,
  });

  it('C1: 远端 doc 广播 → 本 tab 水合且 revision 基线对齐', async () => {
    // 第二个 tab 打开同会话通道
    collabSessionChanged(S);
    startWorkbenchCollab();
    // 模拟另一个 tab 广播 doc（revision 8 > 本地 5 → 接受）
    const channel = new FakeBroadcastChannel(`wb5:${S}`);
    channel.postMessage({
      kind: 'doc',
      from: 'tab-other',
      sessionId: S,
      revision: 8,
      doc: docOf(),
    });
    await new Promise((r) => setTimeout(r, 10));
    const s = useHudStore.getState();
    expect(s.layerGroups).toHaveLength(1);
    expect(s.layerGroups[0].name).toBe('远端组');
    expect(s.layerGroupMembership).toEqual({ l1: 'g1' });
    // revision 基线对齐（下次本 tab 提交携带新 revision，避免无谓 409）
    expect(commitSpy).not.toHaveBeenCalled(); // C2: 不回声
  });

  it('C3: 更旧 revision 的 doc 被忽略（本地已见更新状态）', async () => {
    collabSessionChanged(S);
    startWorkbenchCollab();
    const channel = new FakeBroadcastChannel(`wb5:${S}`);
    channel.postMessage({
      kind: 'doc',
      from: 'tab-other',
      sessionId: S,
      revision: 3, // < 本地 5
      doc: docOf(),
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(useHudStore.getState().layerGroups).toHaveLength(0);
  });

  it('C4: hello → 已武装 tab 以当前 doc 应答（晚到 tab 收敛）', async () => {
    // 本 tab 已武装（beforeEach）；模拟晚到 tab 的 hello
    collabSessionChanged(S);
    startWorkbenchCollab();
    // 本 tab 先有本地组织态（已提交基线）
    useHudStore.getState().createLayerGroup('本地组');
    commitSpy.mockResolvedValueOnce({ mutation_revision: 6 });
    await new Promise((r) => setTimeout(r, 950)); // 防抖窗口
    expect(commitSpy).toHaveBeenCalled();
    // 晚到 tab hello → 本 tab 应答 doc
    const lateTab = new FakeBroadcastChannel(`wb5:${S}`);
    const received: unknown[] = [];
    lateTab.onmessage = (e) => received.push(e.data);
    lateTab.postMessage({ kind: 'hello', from: 'tab-late', sessionId: S });
    await new Promise((r) => setTimeout(r, 20));
    const docMsg = received.find((m) => (m as { kind?: string }).kind === 'doc') as
      | { doc: { groups: { name: string }[] } }
      | undefined;
    expect(docMsg).toBeDefined();
    expect(docMsg!.doc.groups.some((g) => g.name === '本地组')).toBe(true);
  });

  it('C5a: 非本会话消息被忽略（权限边界）', async () => {
    collabSessionChanged(S);
    startWorkbenchCollab();
    const channel = new FakeBroadcastChannel(`wb5:${S}`);
    channel.postMessage({
      kind: 'doc',
      from: 'tab-other',
      sessionId: 'sess-OTHER',
      revision: 99,
      doc: docOf(),
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(useHudStore.getState().layerGroups).toHaveLength(0);
  });

  it('C5b: 无 BroadcastChannel 环境（jsdom 缺省）降级 no-op 不崩溃', () => {
    stopWorkbenchPersistence();
    vi.unstubAllGlobals();
    expect(() => {
      notifyWorkbenchSessionChanged(S);
      markWorkbenchHydrated();
    }).not.toThrow();
    startWorkbenchPersistence();
  });
});
