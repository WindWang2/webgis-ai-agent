/**
 * Map Operation Receipt Bus 单测（ADR-0278）：会话守卫 / 有界 FIFO /
 * 幂等去重 / 状态更新 / 订阅通知。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
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

// ── ADR-0283 D6：agent 可逆回执 → undo 栈录制 ─────────────────────────

describe('agent receipt undo recording (ADR-0283 D6)', () => {
  beforeEach(() => {
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
