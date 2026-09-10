/**
 * Workbench doc 持久化适配器（Workbench V5 W3 → V6 增量通道）。
 *
 * 组织态（分组树/成员/锁/模式）经既有 MapSpec mutation 通道（CAS 串行链 +
 * session 锁 + provenance）落盘到 `mapspec.workbench` 分支 —— 本模块不建
 * 第二真相（无 localStorage 投影、无独立端点）：
 *   - hydrateWorkbenchFromSpec：恢复路径把 spec.workbench 归一化后水合 store；
 *   - startWorkbenchPersistence：store 订阅（切片引用变化门控）+ 防抖提交
 *     （armed 门：恢复完成前绝不提交，防会话切换竞态把空 doc 盖掉新会话的
 *     服务器端 doc）。
 *
 * V6（ADR-0119 / R1-C2/M5）：
 *   - 提交默认走 `patch_workbench_delta`（结构化 diff，O(changed)）；
 *     diff 失败/超限/含 mode 变更 → 回退全量 `patch_workbench_state`；
 *   - 全量提交携带 base_workbench_revision（服务端 doc `_rev` 盖章比对），
 *     堵「游标 revision 被无关 mutation 推进后陈旧全量 doc 静默覆盖」窗口；
 *   - 409 superseded：回灌服务端真相为基线 + 在飞 delta 在服务端 doc 上
 *     重放一次（bounded 单次 rebase）；再冲突 → 显式冲突态（collab store）。
 *   - hydrateRemoteWorkbenchDoc：协作通道（WS/BC）的远端已提交 doc 落地
 *     入口 —— 水合 + 基线/workbench revision 对齐（revision 单调门控）。
 */
import { useHudStore } from '@/lib/store/useHudStore';
import { buildWorkbenchDoc } from '@/lib/store/slices/workbenchSlice';
import { getMapSpecSessionCursor, setMapSpecRevision } from '@/lib/mapspec/session-cursor';
import { normalizeWorkbenchDoc, workbenchDocBytes, WORKBENCH_DOC_MAX_BYTES, type WorkbenchDocV5 } from './doc';
import { applyWorkbenchDelta, diffWorkbenchDocs, type WorkbenchDelta } from './delta';
import {
  bindCollabAdapters,
  collabBroadcastDoc,
  collabSessionChanged,
  startWorkbenchCollab,
  stopWorkbenchCollab,
} from './collab';
import { collabSetConflict } from '@/lib/collab/store';
import { devOnly } from '@/lib/utils/logger';

const PERSIST_DEBOUNCE_MS = 800;

let armed = false;
let targetSessionId: string | null = null;
let lastCommittedJson = '';
/** 服务端 doc 的 `_rev` 盖章（workbench 级 CAS 锚点；-1 = 未知/无盖章）。 */
let lastWorkbenchRevision = -1;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let inflight = false;
let dirtyAfterInflight = false;
let rebaseAttempts = 0;
let unsubscribe: (() => void) | null = null;
let oversizeToastShown = false;

/** 组织态四个源切片的引用门（R2-M4：无关 store 变更不触发序列化）。 */
interface DocSlices {
  groups: unknown;
  membership: unknown;
  locks: unknown;
  mode: unknown;
}

function currentSlices(): DocSlices {
  const s = useHudStore.getState();
  return {
    groups: s.layerGroups,
    membership: s.layerGroupMembership,
    locks: s.lockedLayerIds,
    mode: s.mode,
  };
}

function slicesChanged(a: DocSlices, b: DocSlices): boolean {
  return (
    a.groups !== b.groups
    || a.membership !== b.membership
    || a.locks !== b.locks
    || a.mode !== b.mode
  );
}

function currentDocJson(): string {
  return JSON.stringify(buildWorkbenchDoc(useHudStore.getState()));
}

/** 会话切换时调用（恢复流程）：重置武装门，此前排队的防抖提交全部作废。 */
export function notifyWorkbenchSessionChanged(sessionId: string | null): void {
  targetSessionId = sessionId;
  armed = false;
  lastCommittedJson = '';
  lastWorkbenchRevision = -1;
  rebaseAttempts = 0;
  if (debounceTimer != null) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  // W5：协同通道随会话切换（晚到 tab 发 hello 请求当前 doc）。
  collabSessionChanged(sessionId);
}

/**
 * 恢复完成：以当前 store 快照为已提交基线并武装提交门。spec 无 workbench
 * 分支（旧会话）同样调用 —— 用户此后的组织态编辑从空基线开始持久化。
 */
export function markWorkbenchHydrated(): void {
  lastCommittedJson = currentDocJson();
  armed = true;
  startWorkbenchCollab();
}

/** 恢复路径入口：归一化 spec.workbench → store 水合 → 武装提交门。 */
export function hydrateWorkbenchFromSpec(
  mapspec: Record<string, unknown> | undefined | null,
): boolean {
  const doc = normalizeWorkbenchDoc(mapspec?.workbench);
  // `_rev` 盖章（服务端 V6 起写入；旧会话无盖章 → -1）。
  const rev = (mapspec?.workbench as { _rev?: unknown } | undefined)?._rev;
  lastWorkbenchRevision = typeof rev === 'number' && Number.isFinite(rev) ? rev : -1;
  const ok = useHudStore.getState().hydrateWorkbenchDoc(doc);
  markWorkbenchHydrated();
  return ok;
}

/**
 * V6 协作通道落地口（adopt 调用）：远端**已提交** doc → 水合 + 基线对齐。
 * revision 单调门控：更旧 = 已被服务端 CAS 淘汰的写者，忽略。
 */
export function hydrateRemoteWorkbenchDoc(doc: WorkbenchDocV5, revision: number): boolean {
  const { sessionId } = getMapSpecSessionCursor();
  if (sessionId == null) return false;
  // R1-m5：会话恢复（restore）尚未完成时不得受理远端 doc —— 否则远端水合
  // 的 armed=true 会让恢复流程随后套用更旧 spec 快照时触发提交/409 抖动。
  if (!armed || targetSessionId !== sessionId) return false;
  // 服务端 mutation revision（事件 seq/revision 语义）与本地游标对齐：
  // 旧事件不得把游标拉回（ST-P3-1 同向）。
  if (Number.isFinite(revision) && revision >= 0) setMapSpecRevision(revision);
  // workbench 级 CAS 锚点：优先 doc._rev 盖章，缺省回退事件 revision。
  const rev = (doc as unknown as { _rev?: unknown })._rev;
  const nextWorkbenchRev = typeof rev === 'number' && Number.isFinite(rev) ? rev : revision;
  if (nextWorkbenchRev < lastWorkbenchRevision) return false; // 陈旧写者
  const normalized = normalizeWorkbenchDoc(doc);
  if (normalized == null) return false; // 非法远端载荷：保持当前态（防御）
  const hydrated = useHudStore.getState().hydrateWorkbenchDoc(normalized);
  lastCommittedJson = currentDocJson();
  lastWorkbenchRevision = nextWorkbenchRev;
  // R1-M5：不在此重置 rebaseAttempts —— 造成我方 409 的他人提交，其回声
  // 会在两次提交之间到达并清零计数器，使「单次 rebase」限定失效。重置点
  // 只保留：提交成功（handleCommitResult）与会话切换（notify）。
  armed = true;
  return hydrated;
}

/**
 * V6 协作通道落地口（delta 变体，adopt 调用）：远端 delta 在当前 store doc
 * 上应用 → hydrate。绝对值语义保证重放幂等；引用漂移（delta 引用本地没有
 * 的组）返回 false（调用方回退权威对账）。armed 门与 hydrateRemote 一致。
 */
export function applyRemoteWorkbenchDelta(
  delta: WorkbenchDelta,
  revision: number,
): boolean {
  const { sessionId } = getMapSpecSessionCursor();
  if (sessionId == null || !armed || targetSessionId !== sessionId) return false;
  try {
    const current = buildWorkbenchDoc(useHudStore.getState());
    const next = applyWorkbenchDelta(current, delta);
    return hydrateRemoteWorkbenchDoc(next, revision);
  } catch {
    return false; // 引用漂移等 → 调用方 refetch
  }
}

function scheduleCommit(precomputedJson?: string): void {
  if (!armed || targetSessionId == null) return;
  const json = precomputedJson ?? currentDocJson();
  if (json === lastCommittedJson) return;
  if (debounceTimer != null) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    void commitNow();
  }, PERSIST_DEBOUNCE_MS);
}

async function commitNow(): Promise<void> {
  if (inflight) {
    dirtyAfterInflight = true;
    return;
  }
  const { sessionId } = getMapSpecSessionCursor();
  if (!sessionId || sessionId !== targetSessionId) return;
  const s = useHudStore.getState();
  const doc: WorkbenchDocV5 = buildWorkbenchDoc(s);
  const json = JSON.stringify(doc);
  if (json === lastCommittedJson) return;
  // 体积闸的客户端预检（与后端同款上限，真实 UTF-8 字节口径）：超限拒绝并
  // 给一次性用户可见提示（V5 R2-C1 —— 静默失效会让持久化整体停摆）。
  if (workbenchDocBytes(doc) > WORKBENCH_DOC_MAX_BYTES) {
    devOnly.warn('[workbench-persist] doc exceeds size cap; not committed');
    if (!oversizeToastShown) {
      oversizeToastShown = true;
      try {
        const { useToastStore } = await import('@/components/ui/toast');
        useToastStore.getState().addToast(
          '工作台组织状态过大，已暂停自动保存（分组/锁定等刷新后可能丢失）——请减少分组数量或图层入组规模',
          'warning',
        );
      } catch { /* toast 不可用不得影响状态收敛 */ }
    }
    return;
  }
  inflight = true;
  try {
    const { commitMapSpecMutation } = await import('@/lib/mapspec/user-mutation');

    // 基线 doc（上次已提交真相）→ 结构化 delta（O(changed)）。
    let delta: WorkbenchDelta | null = null;
    try {
      const baseline = normalizeWorkbenchDoc(lastCommittedJson === '' ? null : JSON.parse(lastCommittedJson));
      delta = baseline == null ? null : diffWorkbenchDocs(baseline, doc);
    } catch {
      delta = null;
    }

    if (delta != null) {
      const result = await commitMapSpecMutation({
        intent: 'patch_workbench_delta',
        delta,
      } as never);
      const handled = handleCommitResult(result, doc, json, delta);
      if (handled) return;
    }

    // 回退/兜底：全量 doc + workbench 级 CAS（base_workbench_revision）。
    const result = await commitMapSpecMutation({
      intent: 'patch_workbench_state',
      doc,
      ...(lastWorkbenchRevision >= 0 ? { base_workbench_revision: lastWorkbenchRevision } : {}),
    } as never);
    handleCommitResult(result, doc, json, null);
  } catch (err) {
    // 其它错误保脏，由下一次组织态编辑或恢复重试驱动，不打断用户。
    devOnly.warn('[workbench-persist] commit failed (kept dirty):', err);
  } finally {
    inflight = false;
    if (dirtyAfterInflight) {
      dirtyAfterInflight = false;
      scheduleCommit();
    }
  }
}

/**
 * 提交结果处理（delta 与全量共用）：
 * - 成功：写基线 + workbench CAS 锚点 + BC 广播（同浏览器快路径保留）；
 * - 409 superseded：回灌服务端真相 → 在飞 delta 于服务端 doc 上重放一次
 *   （bounded 单次 rebase，R1-M5）；再冲突 → 显式冲突态。
 * 返回 true = 已处理（含回灌后的重提交排程），调用方不再走全量路径。
 */
function handleCommitResult(
  result: unknown,
  localDoc: WorkbenchDocV5,
  localJson: string,
  inflightDelta: WorkbenchDelta | null,
): boolean {
  const asRecord = result as
    | { status?: string; mutation_revision?: number; mapspec?: Record<string, unknown> }
    | undefined;
  if (asRecord === undefined) return false;

  if (asRecord.status === 'superseded') {
    // superseded ≠ 提交成功（V5 R1-C1）：回灌服务端真相为基线。
    if (typeof asRecord.mutation_revision === 'number') {
      setMapSpecRevision(asRecord.mutation_revision);
    }
    // R1-M4：先捕获本地脏态（hydrate 会覆盖 store）—— 全量路径的 409
    // 同样可 rebase：本地 doc 对服务端 doc 的 diff 即在飞编辑集。
    let localDirty: WorkbenchDocV5 | null = null;
    try {
      localDirty = buildWorkbenchDoc(useHudStore.getState());
    } catch {
      localDirty = null;
    }
    const serverDoc = normalizeWorkbenchDoc(asRecord.mapspec?.workbench);
    if (serverDoc != null) {
      const serverRev = readWorkbenchRev(asRecord.mapspec?.workbench)
        ?? (typeof asRecord.mutation_revision === 'number' ? asRecord.mutation_revision : -1);
      useHudStore.getState().hydrateWorkbenchDoc(serverDoc);
      lastCommittedJson = currentDocJson();
      lastWorkbenchRevision = serverRev;
      // bounded 单次 rebase：在飞编辑集在服务端 doc 上重放 → 重新提交。
      // delta 通道用其自身在飞 delta；全量通道用「本地脏态 vs 服务端 doc」
      // 的结构化 diff（R1-M4 —— 全量 409 不再静默丢弃本地编辑）。
      let rebaseCandidate = inflightDelta;
      if (rebaseCandidate == null && localDirty != null) {
        try {
          rebaseCandidate = diffWorkbenchDocs(serverDoc, localDirty);
        } catch {
          rebaseCandidate = null;
        }
      }
      if (rebaseCandidate != null && rebaseAttempts < 1) {
        rebaseAttempts += 1;
        try {
          // 基线 = 服务端真相（此刻 store 就是服务端 doc）—— 必须在写回
          // rebased 之前捕获，否则重提交会 diff 出空集（静默丢本地编辑）。
          lastCommittedJson = currentDocJson();
          const rebased = applyWorkbenchDelta(serverDoc, rebaseCandidate);
          useHudStore.getState().hydrateWorkbenchDoc(rebased);
          scheduleCommit();
          return true;
        } catch {
          // 重放非法（引用漂移）→ 放弃本地在飞编辑，进入显式冲突态。
        }
      }
      if (rebaseCandidate != null) {
        void collabSetConflictAsync(
          '组织状态与他人修改冲突，已同步服务器最新状态；你刚才的调整未自动应用，请重试。',
        );
      }
    } else {
      lastCommittedJson = '';
      lastWorkbenchRevision = -1;
    }
    return true;
  }

  // 成功：写基线 + CAS 锚点 + BC 广播（CAS revision 随行）。
  lastCommittedJson = localJson;
  rebaseAttempts = 0;
  const serverWbRev = readWorkbenchRev(asRecord.mapspec?.workbench);
  if (serverWbRev != null) lastWorkbenchRevision = serverWbRev;
  else if (typeof asRecord.mutation_revision === 'number') lastWorkbenchRevision = asRecord.mutation_revision;
  const revision = asRecord.mutation_revision;
  if (typeof revision === 'number') {
    collabBroadcastDoc(localDoc, revision);
  }
  return true;
}

function readWorkbenchRev(branch: unknown): number | null {
  const rev = (branch as { _rev?: unknown } | null | undefined)?._rev;
  return typeof rev === 'number' && Number.isFinite(rev) ? rev : null;
}

async function collabSetConflictAsync(message: string): Promise<void> {
  collabSetConflict(message);
  try {
    const { useToastStore } = await import('@/components/ui/toast');
    useToastStore.getState().addToast(message, 'warning');
  } catch { /* toast 不可用不影响冲突态 */ }
}

// 协同适配器绑定（模块加载一次；persistence ↔ collab 单向依赖）。
// hello 应答只回「已提交基线」—— 防抖窗口内未落盘的本地编辑不得冒充
// 已提交真相被晚到 tab 吸收为基线（V5 R1-m7）。
bindCollabAdapters({
  adoptDoc: (doc: WorkbenchDocV5, revision: number): void => {
    hydrateRemoteWorkbenchDoc(doc, revision);
  },
  currentDoc: () => {
    if (!armed || lastCommittedJson === '') return null;
    try {
      return {
        doc: JSON.parse(lastCommittedJson) as WorkbenchDocV5,
        revision: lastWorkbenchRevision >= 0
          ? lastWorkbenchRevision
          : getMapSpecSessionCursor().revision,
      };
    } catch {
      return null;
    }
  },
});

/** 启动 store 订阅（app bootstrap 一次）。重复调用幂等。 */
export function startWorkbenchPersistence(): void {
  if (unsubscribe != null) return;
  let prevSlices = currentSlices();
  let prevJson = currentDocJson();
  unsubscribe = useHudStore.subscribe((state) => {
    // R2-M4：切片引用门 —— presentation/chat/视口等无关变更零序列化成本。
    const slices: DocSlices = {
      groups: state.layerGroups,
      membership: state.layerGroupMembership,
      locks: state.lockedLayerIds,
      mode: state.mode,
    };
    if (!slicesChanged(slices, prevSlices)) return;
    prevSlices = slices;
    const json = JSON.stringify(buildWorkbenchDoc(state));
    if (json === prevJson) return;
    prevJson = json;
    scheduleCommit(json);
  });
}

/** 测试隔离用。 */
export function stopWorkbenchPersistence(): void {
  unsubscribe?.();
  unsubscribe = null;
  if (debounceTimer != null) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  armed = false;
  targetSessionId = null;
  lastCommittedJson = '';
  lastWorkbenchRevision = -1;
  inflight = false;
  dirtyAfterInflight = false;
  oversizeToastShown = false;
  rebaseAttempts = 0;
  stopWorkbenchCollab();
}

/** 测试断言辅助：当前防抖基线。 */
export function workbenchPersistenceArmed(): boolean {
  return armed;
}

/** 测试断言辅助：workbench 级 CAS 锚点。 */
export function workbenchRevisionBaseline(): number {
  return lastWorkbenchRevision;
}

/**
 * V5 W11：页面卸载（pagehide）前尽力冲刷未落盘的 doc 变更 —— 防抖窗口
 * （800ms）内的组织态编辑随页面死亡丢失。best-effort：fetch 可能因卸载
 * 中断，服务端 CAS 保证不产生半提交。
 */
export function flushWorkbenchDoc(): void {
  if (debounceTimer != null) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  if (!armed || targetSessionId == null || inflight) return;
  if (currentDocJson() === lastCommittedJson) return;
  void commitNow();
}
