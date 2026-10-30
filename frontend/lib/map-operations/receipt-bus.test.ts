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
