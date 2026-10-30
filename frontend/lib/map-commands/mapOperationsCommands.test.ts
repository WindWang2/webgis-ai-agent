/**
 * map_operation_receipt 命令测试（ADR-0278）：目录注册（#535 契约）+
 * 处理器行为（bus 投递 / 跨会话拒绝 / 形状校验）。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { COMMAND_CATALOGUE } from './catalogue';
import { mapOperationsCommands } from './mapOperationsCommands';
import {
  getOperationReceipts,
  setReceiptBusSession,
} from '@/lib/map-operations/receipt-bus';

function ctxWith(params: Record<string, unknown>) {
  return { params } as never;
}

describe('map_operation_receipt command', () => {
  beforeEach(() => {
    // 先解绑再绑定 —— setReceiptBusSession 同名 no-op 不清空，跨用例
    // 残留回执会污染长度断言。
    setReceiptBusSession(null);
    setReceiptBusSession('s1');
  });

  it('is registered in the catalogue (#535 ghost-command gate)', () => {
    expect(COMMAND_CATALOGUE.map_operation_receipt).toBeDefined();
    expect(mapOperationsCommands.map_operation_receipt).toBe(
      COMMAND_CATALOGUE.map_operation_receipt,
    );
  });

  it('delivers a valid receipt to the bus and acks succeeded', () => {
    const res = mapOperationsCommands.map_operation_receipt.run(
      ctxWith({
        session_id: 's1',
        receipt: {
          operation_id: 'mop-cmd-1',
          status: 'committed',
          effects: [
            { index: 0, kind: 'set_layer_visibility', targets: ['heat'], state: 'applied' },
          ],
        },
      }),
    );
    expect(res).toMatchObject({ status: 'succeeded' });
    expect(getOperationReceipts()[0].operation_id).toBe('mop-cmd-1');
  });

  it('fails the ack for a cross-session receipt (no silent fake success)', () => {
    const res = mapOperationsCommands.map_operation_receipt.run(
      ctxWith({
        session_id: 's2',
        receipt: { operation_id: 'mop-cmd-2', status: 'committed' },
      }),
    );
    expect(res).toMatchObject({ status: 'failed', error: 'receipt_not_acceptable' });
    expect(getOperationReceipts()).toHaveLength(0);
  });

  it('validator requires a receipt with operation_id', () => {
    const validator = mapOperationsCommands.map_operation_receipt.requiredParams;
    expect(validator({ receipt: { operation_id: 'x' } })).toBe(true);
    expect(validator({})).toBe(false);
    expect(validator({ receipt: { nope: 1 } })).toBe(false);
  });
});
