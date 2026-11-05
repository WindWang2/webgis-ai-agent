/**
 * Map Operation Receipt Bus —— agent 地图操作回执的前端单例通道（ADR-0278）。
 *
 * 数据流：后端 webgis_map_operation 工具结果（SSE step_result）→
 * dispatchAction 命令 `map_operation_receipt`（catalogue 注册）→ 本总线
 * → MapHarnessCards 渲染。设计仿 session-cursor 模块单例（不进 hud
 * store，避免与 #1871/#1873 的 slice 改动面冲突）。
 *
 * 纪律：
 * - 会话守卫（INV-2 同款）：跨会话回执直接丢弃，绝不渲染到别的会话；
 * - 有界：最多保留 4 张卡（FIFO，旧的滚出）；
 * - 确认完成后由卡面按 operation_id 更新状态（不是删除 —— 操作历史
 *   可对账「已完成动作不重复提交」）。
 */
export interface MapOperationEffectView {
  index: number;
  kind: string;
  target_kind?: string;
  targets?: string[];
  state?: string;
  reason_code?: string;
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
  note?: string;
}

export interface MapOperationReceiptView {
  schema_version?: string;
  operation_id: string;
  mode?: string;
  status?: string;
  message?: string;
  actor?: string;
  base_revision?: number;
  final_revision?: number;
  mutation_revision?: number;
  applied_count?: number;
  skipped_count?: number;
  failed_count?: number;
  untouched_layer_count?: number;
  effects?: MapOperationEffectView[];
  clarification?: {
    question?: string;
    candidates?: Array<{ layer_id: string; name?: string; source?: string }>;
  };
  commit_url?: string;
  replayed?: boolean;
  [key: string]: unknown;
}

const MAX_VISIBLE_RECEIPTS = 4;

let currentSessionId: string | null = null;
let receipts: MapOperationReceiptView[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    try {
      listener();
    } catch {
      /* 单个监听器故障不得阻断其他卡面 */
    }
  }
}

/** 绑定会话（map-panel 挂载/会话切换时调用；切换即清空旧回执）。 */
export function setReceiptBusSession(sessionId: string | null): void {
  if (currentSessionId === sessionId) return;
  currentSessionId = sessionId;
  receipts = [];
  emit();
}

/** 命令处理器入口：接收一条回执（跨会话守卫 + 有界 FIFO）。 */
export function pushOperationReceipt(
  receipt: MapOperationReceiptView | null | undefined,
  sessionId?: string | null,
): boolean {
  if (!receipt || typeof receipt.operation_id !== 'string') return false;
  // 会话守卫：命令执行总在活跃会话上下文；bus 未绑定或跨会话 → 丢弃。
  const boundSession = sessionId ?? currentSessionId;
  if (!boundSession || (currentSessionId && boundSession !== currentSessionId)) {
    return false;
  }
  const next = receipts.filter((r) => r.operation_id !== receipt.operation_id);
  next.unshift(receipt);
  receipts = next.slice(0, MAX_VISIBLE_RECEIPTS);
  recordAgentReceiptUndo(receipt);
  emit();
  return true;
}


/**
 * ADR-0283 D6：agent 可逆回执 → undo 栈录制（反向重放既有 CAS 通道）。
 * 只录 presentation 族（后端 ``invertible`` 披露为 true）；用户可用
 * Ctrl+Z 撤销 agent 的显隐/透明度操作。undo 语义 = 再提交一次反向
 * mutation（ADR-0278「不虚构强原子」）；不可逆效果不录（数据层撤销归
 * feature-edit /undo）。
 */
function recordAgentReceiptUndo(receipt: MapOperationReceiptView): void {
  try {
    if (receipt.invertible !== true) return;
    if (String(receipt.origin ?? 'agent') !== 'agent') return;
    if (receipt.status !== 'committed' && receipt.status !== 'partial') return;
    // 幂等去重（review P3）：同一 operation_id 只录一次。
    const opKey = String(receipt.operation_id);
    if (undoRecordedOps.has(opKey)) return;
    undoRecordedOps.add(opKey);
    const applied = (receipt.effects ?? []).filter(
      (e) => e.state === 'applied' && (e.targets?.length ?? 0) > 0,
    );
    if (!applied.length) return;
    // 多效果回执逐层录制（每层独立可撤销；undo 栈有序）。
    void import('@/lib/workbench/undo').then(({ presentationCommand }) => {
      for (const effect of applied) {
        const layerId = String(effect.targets?.[0] ?? '');
        if (!layerId) continue;
        const before = effect.before as
          | { visible?: boolean; opacity?: number }
          | undefined;
        const after = effect.after as
          | { visible?: boolean; opacity?: number }
          | undefined;
        if (!before || !after) continue;
        presentationCommand(
          `agent:${receipt.operation_id?.slice(0, 12) ?? 'op'}`,
          layerId,
          'agent',
          before,
          after,
        );
      }
    }).catch(() => {
      // undo 录制是增值面，绝不阻塞回执通道。
    });
  } catch {
    // 同上：观察面绝不阻塞。
  }
}

/** 已录制 undo 的操作 id（幂等：SSE 重连/step 重放不双录）。 */
const undoRecordedOps = new Set<string>();

/** 按操作 id 更新状态（owner 批准提交后回填最终回执）。 */
export function updateOperationReceipt(receipt: MapOperationReceiptView): boolean {
  if (!receipt?.operation_id) return false;
  const idx = receipts.findIndex((r) => r.operation_id === receipt.operation_id);
  if (idx < 0) return pushOperationReceipt(receipt);
  const wasPending = receipts[idx].status !== 'committed'
    && receipts[idx].status !== 'partial';
  receipts = [
    ...receipts.slice(0, idx),
    receipt,
    ...receipts.slice(idx + 1),
  ];
  // review P2-6：owner 批准（pending → committed/partial）也是 agent 起源
  // commit 的落定——同样录 undo（D6 承诺覆盖默认确认流）。
  if (wasPending) recordAgentReceiptUndo(receipt);
  emit();
  return true;
}

export function getOperationReceipts(): MapOperationReceiptView[] {
  return receipts;
}

export function dismissOperationReceipt(operationId: string): void {
  receipts = receipts.filter((r) => r.operation_id !== operationId);
  emit();
}

/** 兼容 useSyncExternalStore 的订阅面。 */
export function subscribeReceipts(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
