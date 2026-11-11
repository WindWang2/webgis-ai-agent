/**
 * PR-D：Agent MapSpec 提交进入撤销栈。
 *
 *  A1. SSE 每份更高 revision 的 committed spec 记一条 actor='agent' 命令，
 *      键 = (base_revision, agent_revision]；回声（revision 未前进）不记；
 *  A2. 只带 revision 的事件不吞段首：一轮多笔提交整体成段；
 *  A3. Ctrl+Z（undo()）→ undo_agent_commit user mutation，expected_revision
 *      取串行链轮到时的游标；redo 带 direction=redo；
 *  A4. 服务端拒绝（冲突/过期 400）→ toast 给出服务端 hint，命令回到 undo 栈；
 *  A5. 409 superseded → 回灌真相，命令回到 undo 栈。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  setMapSpecRevision,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import { useToastStore } from '@/components/ui/toast';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  observeAgentSpecEvent,
  resetAgentCommitUndoForTests,
} from './agent-commit-undo';
import { canRedo, canUndo, getUndoStack, redo, resetUndoForTests, undo } from './undo';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();
const SID = 'sid-agent-undo';

function spec(ids: string[]) {
  return { version: '1.0', layers: ids.map((id) => ({ id, type: 'circle', source: `s-${id}` })), sources: {} };
}

function respond(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

/** 等动态 import + 串行链 + 响应处理全部落定（fetch 被调用 n 次后再排空）。 */
async function flush(calls = 1): Promise<void> {
  await vi.waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(calls), { timeout: 3000 });
  for (let i = 0; i < 20; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

/** 模拟 use-sse-stream 的调用序：读 prior → 推进游标 → 提交 spec → 观察。 */
function sseEvent(revision: number, ids?: string[]) {
  const priorRevision = getMapSpecSessionCursor().revision;
  setMapSpecRevision(revision);
  const accepted = ids ? commitMapSpecDocument(spec(ids), revision) : false;
  observeAgentSpecEvent({ sessionId: SID, priorRevision, incomingRevision: revision, specAccepted: accepted });
}

function sentBodies(): Array<Record<string, unknown>> {
  return fetchMock.mock.calls.map((c) => JSON.parse(c[1].body as string));
}

describe('Agent 提交撤销（PR-D）', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    resetUndoForTests();
    resetAgentCommitUndoForTests();
    useToastStore.setState({ toasts: [] });
    useHudStore.setState({ layers: [] });
    setMapSpecSessionCursor(SID, 4);
    commitMapSpecDocument(spec(['A']), 4);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('A1: 新 revision 的 Agent spec 入栈；回声不入栈', () => {
    sseEvent(5, ['A', 'B']);
    expect(getUndoStack()).toHaveLength(1);
    expect(getUndoStack()[0]).toMatchObject({ actor: 'agent', label: 'Agent 地图提交（r5）' });
    sseEvent(5, ['A', 'B']); // 同 revision 回声
    expect(getUndoStack()).toHaveLength(1);
  });

  it('A2: revision-only 事件不吞段首 —— 段 = (4, 7]', async () => {
    sseEvent(6); // 只带 revision
    expect(getUndoStack()).toHaveLength(0);
    sseEvent(7, ['A', 'B', 'C']);
    expect(getUndoStack()).toHaveLength(1);
    fetchMock.mockResolvedValueOnce(respond(200, { success: true, mutation_revision: 8, mapspec: spec(['A']) }));
    undo();
    await flush();
    expect(sentBodies()[0]).toMatchObject({
      intent: 'undo_agent_commit', base_revision: 4, agent_revision: 7,
      direction: 'undo', expected_revision: 7,
    });
  });

  it('A3: undo → redo 走同一通道；成功后游标前进、HUD 行按 spec 修剪', async () => {
    useHudStore.setState({
      layers: [
        { id: 'A', name: 'A', type: 'vector', visible: true, opacity: 1, _mapspecLayerId: 'A' },
        { id: 'B', name: 'B', type: 'vector', visible: true, opacity: 1, _mapspecLayerId: 'B' },
      ] as never,
    });
    sseEvent(5, ['A', 'B']);
    fetchMock.mockResolvedValueOnce(respond(200, { success: true, mutation_revision: 6, mapspec: spec(['A']) }));
    undo();
    await flush();
    expect(getMapSpecSessionCursor().revision).toBe(6);
    expect(useHudStore.getState().layers.map((l) => l.id)).toEqual(['A']);
    expect(canRedo()).toBe(true);

    fetchMock.mockResolvedValueOnce(respond(200, { success: true, mutation_revision: 7, mapspec: spec(['A', 'B']) }));
    redo();
    await flush(2);
    expect(sentBodies()[1]).toMatchObject({
      intent: 'undo_agent_commit', base_revision: 4, agent_revision: 5,
      direction: 'redo', expected_revision: 6,
    });
    expect(canUndo()).toBe(true);
  });

  it('A4: 服务端冲突拒绝 → toast 服务端 hint，命令回到 undo 栈', async () => {
    sseEvent(5, ['A', 'B']);
    fetchMock.mockResolvedValueOnce(respond(400, {
      detail: {
        success: false, error_code: 'AGENT_UNDO_CONFLICT',
        correction_hint: '该 Agent 提交之后地图又有修改（未撤销）',
      },
    }));
    undo();
    await flush();
    expect(canUndo()).toBe(true);
    expect(canRedo()).toBe(false);
    expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
      '该 Agent 提交之后地图又有修改（未撤销）',
    );
  });

  it('A5: 409 superseded → 回灌服务端真相，命令回到 undo 栈', async () => {
    sseEvent(5, ['A', 'B']);
    fetchMock.mockResolvedValueOnce(respond(409, {
      detail: { success: false, status: 'superseded', mutation_revision: 9, mapspec: spec(['A', 'B', 'X']) },
    }));
    undo();
    await flush();
    expect(getMapSpecSessionCursor().revision).toBe(9);
    expect(canUndo()).toBe(true);
    expect(canRedo()).toBe(false);
  });

  it('跨会话：旧会话的段首不会泄漏到新会话', () => {
    sseEvent(6); // SID 段首 = 4
    setMapSpecSessionCursor('other', 10);
    observeAgentSpecEvent({ sessionId: 'other', priorRevision: 10, incomingRevision: 11, specAccepted: true });
    const [cmd] = getUndoStack();
    expect(cmd.label).toBe('Agent 地图提交（r11）');
  });
});
