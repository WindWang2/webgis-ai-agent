/**
 * 地图操作回执命令 slice（ADR-0278）：`map_operation_receipt` 不是地图
 * mutation —— 它把服务端已提交的操作回执投递到前端回执总线（渲染 +
 * 跨刷新对账）。durable 语义由服务端拥有（MapSpec 已落盘），命令面只
 * 负责呈现；fly_to 等伴生命令由后端在同一 commands 列表里单独下发。
 */
import type { CommandEntry, MapCommandResult } from './types';
import {
  pushOperationReceipt,
  type MapOperationReceiptView,
} from '@/lib/map-operations/receipt-bus';

interface ReceiptCommandParams {
  receipt?: MapOperationReceiptView | null;
  session_id?: string | null;
}

export const mapOperationsCommands: Record<string, CommandEntry> = {
  map_operation_receipt: {
    requiredParams: (p) => {
      const params = p as ReceiptCommandParams;
      return Boolean(params?.receipt && typeof params.receipt.operation_id === 'string');
    },
    run(ctx): MapCommandResult {
      const params = (ctx.params || {}) as ReceiptCommandParams;
      const accepted = pushOperationReceipt(
        params.receipt,
        params.session_id ?? null,
      );
      if (!accepted) {
        // 跨会话/形状非法：呈现面守卫拒绝。不是地图执行失败（服务端
        // mutation 已成功），按 failed+typed error 上报 ack，卡面缺席即
        // 用户可见的差异（无假成功）。
        return { status: 'failed', error: 'receipt_not_acceptable' };
      }
      return { status: 'succeeded' };
    },
  },
};
