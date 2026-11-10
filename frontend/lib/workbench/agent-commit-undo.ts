/**
 * PR-D：Agent MapSpec 提交进入撤销栈（Ctrl+Z 撤销一整笔 Agent 地图提交）。
 *
 * - SSE（Agent 回合）每带来一份更高 revision 的 committed MapSpec，就以
 *   ``(base_revision, agent_revision]`` 为键记一条 actor='agent' 的命令。
 *   base 取「上一份已见 spec 之后的游标 revision」—— 只带 revision 不带
 *   spec 的事件不会吞掉段首（一轮多笔提交整体成段）。
 * - undo/redo = user mutation ``undo_agent_commit``（expected_revision CAS，
 *   与其它用户写共用串行链）。冲突判定在服务端锁内：用户在 Agent 提交后
 *   有未撤销的改动 / 段内夹有其它提交 / 撤销点过期 → 400 + error_code，
 *   地图不变；本端 toast 说明原因并把命令放回原栈（不丢撤销能力）。
 * - 成功后按返回的 spec 修剪/补齐 HUD 图层行（被撤销的 Agent 图层消失）。
 */
import { ApiError } from '@/lib/api/transport';
import { useToastStore } from '@/components/ui/toast';
import { devOnly } from '@/lib/utils/logger';
import { recordCommand, restoreAfterFailedReplay } from './undo';

let pendingBase: { sessionId: string; revision: number } | null = null;

/** 测试隔离。 */
export function resetAgentCommitUndoForTests(): void {
  pendingBase = null;
}

/**
 * SSE 事件观察点（use-sse-stream 在推进游标**之前**调用）。
 *
 * @param priorRevision 推进前的游标 revision
 * @param incomingRevision 事件携带的 revision（无则不调用）
 * @param specAccepted 本事件的 spec 已被 commitMapSpecDocument 接受
 */
export function observeAgentSpecEvent(args: {
  sessionId: string | undefined;
  priorRevision: number;
  incomingRevision: number;
  specAccepted: boolean;
  layerIds?: string[];
}): void {
  const { sessionId, priorRevision, incomingRevision, specAccepted } = args;
  if (!sessionId || !Number.isFinite(incomingRevision)) return;
  if (pendingBase && pendingBase.sessionId !== sessionId) pendingBase = null;
  if (incomingRevision > priorRevision && pendingBase == null) {
    pendingBase = { sessionId, revision: priorRevision };
  }
  if (!specAccepted || pendingBase == null) return;
  const base = pendingBase.revision;
  if (incomingRevision <= base) return;
  pendingBase = null;
  recordAgentCommit(sessionId, base, incomingRevision, args.layerIds);
}

function recordAgentCommit(
  sessionId: string,
  baseRevision: number,
  agentRevision: number,
  layerIds?: string[],
): void {
  const label = `Agent 地图提交（r${agentRevision}）`;
  let cmdId = '';
  cmdId = recordCommand({
    label,
    kind: 'style',
    actor: 'agent',
    layerIds: layerIds ?? [],
    undo: () => void replay('undo', { sessionId, baseRevision, agentRevision, cmdId: () => cmdId }),
    redo: () => void replay('redo', { sessionId, baseRevision, agentRevision, cmdId: () => cmdId }),
  });
}

type UserMutationModule = typeof import('@/lib/mapspec/user-mutation');
type RestoreModule = typeof import('@/lib/session/map-state-restore');
let userMutationPromise: Promise<UserMutationModule> | null = null;
let restorePromise: Promise<RestoreModule> | null = null;

function errorDetail(err: unknown): { code?: string; hint?: string } {
  if (!(err instanceof ApiError)) return {};
  const body = err.body as Record<string, unknown> | null | undefined;
  const candidates = [
    body && typeof body === 'object' ? (body as { detail?: unknown }).detail : undefined,
    body && typeof body === 'object' ? (body as { data?: unknown }).data : undefined,
    body,
  ];
  for (const c of candidates) {
    if (c && typeof c === 'object' && ('error_code' in c || 'correction_hint' in c)) {
      const d = c as { error_code?: unknown; correction_hint?: unknown };
      return {
        code: typeof d.error_code === 'string' ? d.error_code : undefined,
        hint: typeof d.correction_hint === 'string' ? d.correction_hint : undefined,
      };
    }
  }
  return {};
}

async function replay(
  direction: 'undo' | 'redo',
  ctx: { sessionId: string; baseRevision: number; agentRevision: number; cmdId: () => string },
): Promise<void> {
  userMutationPromise ??= import('@/lib/mapspec/user-mutation');
  restorePromise ??= import('@/lib/session/map-state-restore');
  try {
    const { commitMapSpecMutation } = await userMutationPromise;
    const data = await commitMapSpecMutation({
      intent: 'undo_agent_commit',
      base_revision: ctx.baseRevision,
      agent_revision: ctx.agentRevision,
      direction,
    });
    if (!data) {
      // 会话已切换（串行链内复核）—— 不回放、不回栈（栈随会话清空）。
      return;
    }
    if ((data as { status?: string }).status === 'superseded') {
      // 并发提交推进了 revision：服务端真相已回灌 + toast；命令放回原栈，
      // 用户可在新状态上再次撤销（服务端会重新判定冲突）。
      restoreAfterFailedReplay(ctx.cmdId(), direction);
      return;
    }
    if (data.mapspec) {
      const { syncSpecLayersToStore } = await restorePromise;
      syncSpecLayersToStore(data.mapspec as never, ctx.sessionId);
    }
  } catch (err) {
    restoreAfterFailedReplay(ctx.cmdId(), direction);
    const { hint } = errorDetail(err);
    devOnly.warn(`[agent-undo] ${direction} refused`, err);
    try {
      useToastStore.getState().addToast(
        hint || (direction === 'undo' ? '无法撤销该 Agent 地图提交' : '无法重做该 Agent 地图提交'),
        'warning',
      );
    } catch { /* toast 不可用不影响状态 */ }
  }
}
