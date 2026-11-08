/**
 * Map Operation Receipt Bus 单测（ADR-0278）：会话守卫 / 有界 FIFO /
 * 幂等去重 / 状态更新 / 订阅通知。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  dismissOperationReceipt,
  getOperationReceipts,
  pushOperationReceipt,
  setReceiptBusSession,
  subscribeReceipts,
  updateOperationReceipt,
} from './receipt-bus';

function receipt(operationId: string, status = 'committed') {
  return {
    operation_id: operationId,
    status,
    message: 'ok',
    effects: [{ index: 0, kind: 'set_layer_visibility', targets: ['a'], state: 'applied' }],
  };
}

describe('receipt-bus', () => {
  beforeEach(() => {
    setReceiptBusSession(null);
  });

  it('rejects malformed receipts', () => {
    setReceiptBusSession('s1');
    expect(pushOperationReceipt(null, 's1')).toBe(false);
    expect(pushOperationReceipt({} as never, 's1')).toBe(false);
    expect(getOperationReceipts()).toHaveLength(0);
  });

  it('drops cross-session receipts (INV-2 discipline)', () => {
    setReceiptBusSession('s1');
    expect(pushOperationReceipt(receipt('mop-1'), 's2')).toBe(false);
    expect(getOperationReceipts()).toHaveLength(0);
    expect(pushOperationReceipt(receipt('mop-2'), 's1')).toBe(true);
    expect(getOperationReceipts()).toHaveLength(1);
  });

  it('dedupes by operation_id (idempotent replay updates, not appends)', () => {
    setReceiptBusSession('s1');
    pushOperationReceipt(receipt('mop-1', 'committed'), 's1');
    pushOperationReceipt(receipt('mop-1', 'committed'), 's1');
    expect(getOperationReceipts()).toHaveLength(1);
  });

  it('keeps at most 4 receipts (FIFO)', () => {
    setReceiptBusSession('s1');
    for (let i = 0; i < 6; i += 1) {
      pushOperationReceipt(receipt(`mop-${i}`), 's1');
    }
    const ids = getOperationReceipts().map((r) => r.operation_id);
    expect(ids).toHaveLength(4);
    expect(ids[0]).toBe('mop-5'); // 最新在前
    expect(ids).not.toContain('mop-0'); // 最旧滚出
  });

  it('clears on session switch', () => {
    setReceiptBusSession('s1');
    pushOperationReceipt(receipt('mop-1'), 's1');
    setReceiptBusSession('s2');
    expect(getOperationReceipts()).toHaveLength(0);
  });

  it('updates a receipt in place (owner approve backfill)', () => {
    setReceiptBusSession('s1');
    pushOperationReceipt(receipt('mop-1', 'confirmation_required'), 's1');
    updateOperationReceipt(receipt('mop-1', 'committed'));
    const first = getOperationReceipts()[0];
    expect(first.status).toBe('committed');
    expect(getOperationReceipts()).toHaveLength(1);
  });

  it('notifies subscribers', () => {
    setReceiptBusSession('s1');
    const listener = vi.fn();
    const unsubscribe = subscribeReceipts(listener);
    pushOperationReceipt(receipt('mop-1'), 's1');
    expect(listener).toHaveBeenCalled();
    listener.mockClear();
    dismissOperationReceipt('mop-1');
    expect(listener).toHaveBeenCalled();
    unsubscribe();
  });
});

// ── ADR-0285 D6：agent 可逆回执 → undo 栈录制 ─────────────────────────

describe('agent receipt undo recording (ADR-0285 D6)', () => {
  beforeEach(() => {
    setMapSpecSessionCursor('sess-rb');
    setReceiptBusSession(null);
    setReceiptBusSession('sess-rb');
  });

  it('records presentationCommand per applied invertible effect', async () => {
    const undoMod = await import('@/lib/workbench/undo');
    const recordSpy = vi.spyOn(undoMod, 'presentationCommand').mockImplementation(() => {});
    const ok = pushOperationReceipt({
      operation_id: 'op-agent-1',
      status: 'committed',
      origin: 'agent',
      invertible: true,
      effects: [
        {
          index: 0, kind: 'set_layer_visibility', targets: ['heatmap'],
          state: 'applied', before: { visible: true }, after: { visible: false },
        },
        {
          index: 1, kind: 'set_layer_opacity', targets: ['schools'],
          state: 'applied', before: { opacity: 1 }, after: { opacity: 0.5 },
        },
      ],
    }, 'sess-rb');
    expect(ok).toBe(true);
    await vi.waitFor(() => {
      expect(recordSpy).toHaveBeenCalledTimes(2);
    });
    expect(recordSpy).toHaveBeenCalledWith(
      expect.stringContaining('agent:'),
      'heatmap', 'agent',
      { visible: true }, { visible: false },
    );
    recordSpy.mockRestore();
  });

  it('records styleCommand for symbol/paint effects with numeric before (SW-04)', async () => {
    const undoMod = await import('@/lib/workbench/undo');
    const styleSpy = vi.spyOn(undoMod, 'styleCommand').mockImplementation(() => {});
    const ok = pushOperationReceipt({
      operation_id: 'op-agent-style-1',
      status: 'committed',
      origin: 'agent',
      invertible: true,
      effects: [
        {
          index: 0, kind: 'set_layer_symbol', targets: ['schools'],
          state: 'applied', before: { 'circle-radius': 4 }, after: { 'circle-radius': 8 },
        },
        {
          index: 1, kind: 'set_layer_paint', targets: ['heatmap'],
          state: 'applied', before: { 'heatmap-opacity': 0.8 }, after: { 'heatmap-opacity': 0.3 },
        },
      ],
    }, 'sess-rb');
    expect(ok).toBe(true);
    await vi.waitFor(() => {
      expect(styleSpy).toHaveBeenCalledTimes(2);
    });
    expect(styleSpy).toHaveBeenCalledWith(
      expect.stringContaining('agent:'),
      'schools', 'agent',
      { 'circle-radius': 4 }, { 'circle-radius': 8 },
    );
    styleSpy.mockRestore();
  });

  it('does not record styleCommand when before value is missing (SW-04 honesty)', async () => {
    const undoMod = await import('@/lib/workbench/undo');
    const styleSpy = vi.spyOn(undoMod, 'styleCommand').mockImplementation(() => {});
    pushOperationReceipt({
      operation_id: 'op-agent-style-2',
      status: 'committed',
      origin: 'agent',
      invertible: true,
      effects: [
        {
          index: 0, kind: 'set_layer_symbol', targets: ['schools'],
          state: 'applied', before: { 'text-size': null }, after: { 'text-size': 14 },
        },
      ],
    }, 'sess-rb');
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(styleSpy).not.toHaveBeenCalled();
    styleSpy.mockRestore();
  });

  it('skips non-invertible or user-origin receipts', async () => {
    const undoMod = await import('@/lib/workbench/undo');
    const recordSpy = vi.spyOn(undoMod, 'presentationCommand').mockImplementation(() => {});
    pushOperationReceipt({
      operation_id: 'op-agent-2', status: 'committed', origin: 'agent',
      invertible: false,
      effects: [{ index: 0, kind: 'remove_layer', targets: ['x'], state: 'applied' }],
    }, 'sess-rb');
    pushOperationReceipt({
      operation_id: 'op-user-1', status: 'committed', origin: 'user',
      invertible: true,
      effects: [{
        index: 0, kind: 'set_layer_visibility', targets: ['y'],
        state: 'applied', before: { visible: true }, after: { visible: false },
      }],
    }, 'sess-rb');
    await new Promise((r) => setTimeout(r, 20));
    expect(recordSpy).not.toHaveBeenCalled();
    recordSpy.mockRestore();
  });
});

describe('owner confirm path undo recording (review P2-6)', () => {
  beforeEach(() => {
    setMapSpecSessionCursor('sess-rb2');
    setReceiptBusSession(null);
    setReceiptBusSession('sess-rb2');
  });

  it('records undo when pending receipt transitions to committed via updateOperationReceipt', async () => {
    const undoMod = await import('@/lib/workbench/undo');
    const recordSpy = vi.spyOn(undoMod, 'presentationCommand').mockImplementation(() => {});
    const pending = {
      operation_id: 'op-confirm-1', status: 'confirmation_required', origin: 'agent',
      invertible: true, effects: [{
        index: 0, kind: 'set_layer_visibility', targets: ['heatmap'],
        state: 'applied', before: { visible: true }, after: { visible: false },
      }],
    };
    pushOperationReceipt(pending, 'sess-rb2');
    const committed = { ...pending, status: 'committed' };
    updateOperationReceipt(committed);
    await vi.waitFor(() => {
      expect(recordSpy).toHaveBeenCalledTimes(1);
    });
    // 同 op 重放（SSE 重连）不双录。
    updateOperationReceipt(committed);
    await new Promise((r) => setTimeout(r, 20));
    expect(recordSpy).toHaveBeenCalledTimes(1);
    recordSpy.mockRestore();
  });
});


it('drops deferred undo recording after session change and permits the same operation id in a new session', async () => {
  const undo = await import('@/lib/workbench/undo');
  const spy = vi.spyOn(undo, 'presentationCommand').mockImplementation(() => {});
  setMapSpecSessionCursor('race-a'); setReceiptBusSession('race-a');
  const r = { operation_id: 'same-id', status: 'committed', origin: 'agent', invertible: true,
    effects: [{ index: 0, kind: 'set_layer_visibility', targets: ['a'], state: 'applied', before: { visible: true }, after: { visible: false } }] };
  pushOperationReceipt(r, 'race-a');
  setMapSpecSessionCursor('race-b'); setReceiptBusSession('race-b');
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(spy).not.toHaveBeenCalled();
  expect(updateOperationReceipt(r, 'race-a')).toBe(false);
  pushOperationReceipt(r, 'race-b');
  await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  spy.mockRestore();
});

it('bounds undo replay deduplication and releases old operation identities', async () => {
  const undo = await import('@/lib/workbench/undo');
  const spy = vi.spyOn(undo, 'presentationCommand').mockImplementation(() => {});
  setMapSpecSessionCursor('bounds'); setReceiptBusSession('bounds');
  for (let i = 0; i < 130; i++) {
    pushOperationReceipt({ operation_id: `bounded-${i}`, status: 'committed', origin: 'agent', invertible: true,
      effects: [{ index: 0, kind: 'set_layer_visibility', targets: ['a'], state: 'applied', before: { visible: true }, after: { visible: false } }] }, 'bounds');
  }
  await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(130));
  pushOperationReceipt({ operation_id: 'bounded-0', status: 'committed', origin: 'agent', invertible: true,
    effects: [{ index: 0, kind: 'set_layer_visibility', targets: ['a'], state: 'applied', before: { visible: true }, after: { visible: false } }] }, 'bounds');
  await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(131));
  spy.mockRestore();
});
