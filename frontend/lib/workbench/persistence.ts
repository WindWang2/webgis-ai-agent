/**
 * Workbench doc 持久化适配器（Workbench V5 / W3）。
 *
 * 组织态（分组树/成员/锁/模式）经既有 MapSpec mutation 通道（CAS 串行链 +
 * session 锁 + provenance）落盘到 `mapspec.workbench` 分支 —— 本模块不建
 * 第二真相（无 localStorage 投影、无独立端点）：
 *   - hydrateWorkbenchFromSpec：恢复路径把 spec.workbench 归一化后水合 store；
 *   - startWorkbenchPersistence：store 订阅（切片引用变化门控）+ 防抖提交
 *     （armed 门：恢复完成前绝不提交，防会话切换竞态把空 doc 盖掉新会话的
 *     服务器端 doc）。
 * - 409 superseded（R1-C1/R2-M2 修复）：superseded 返回**不是**提交成功 ——
 *   不写基线、不广播；从响应 mapspec.workbench 回灌服务端真相（workbench
 *   分支不在 applyCommittedMapSpec 的 layers 回灌范围内）后以服务端真相为
 *   基线；本地若有更新编辑由订阅再驱动重提交。
 */
import { useHudStore } from '@/lib/store/useHudStore';
import { buildWorkbenchDoc } from '@/lib/store/slices/workbenchSlice';
import { getMapSpecSessionCursor, setMapSpecRevision } from '@/lib/mapspec/session-cursor';
import { normalizeWorkbenchDoc, workbenchDocBytes, WORKBENCH_DOC_MAX_BYTES, type WorkbenchDocV5 } from './doc';
import {
  bindCollabAdapters,
  collabBroadcastDoc,
  collabSessionChanged,
  startWorkbenchCollab,
  stopWorkbenchCollab,
} from './collab';
import { devOnly } from '@/lib/utils/logger';

const PERSIST_DEBOUNCE_MS = 800;

let armed = false;
let targetSessionId: string | null = null;
let lastCommittedJson = '';
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let inflight = false;
let dirtyAfterInflight = false;
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
  const ok = useHudStore.getState().hydrateWorkbenchDoc(doc);
  markWorkbenchHydrated();
  return ok;
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
  // 给一次性用户可见提示（R2-C1 —— 静默失效会让组织态持久化整体停摆）。
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
    const result = await commitMapSpecMutation({
      intent: 'patch_workbench_state',
      doc,
    });
    const asRecord = result as
      | { status?: string; mutation_revision?: number; mapspec?: Record<string, unknown> }
      | undefined;
    if (asRecord !== undefined && asRecord?.status === 'superseded') {
      // R1-C1/R2-M2：superseded ≠ 提交成功。服务端 CAS 拒绝了本 doc ——
      // 不写基线、不广播；从响应 mapspec.workbench 回灌服务端真相并以其为
      // 基线（该分支不在 commitMapSpecMutation 的 layers 回灌范围内）。
      if (typeof asRecord.mutation_revision === 'number') {
        setMapSpecRevision(asRecord.mutation_revision);
      }
      const serverDoc = normalizeWorkbenchDoc(asRecord.mapspec?.workbench);
      if (serverDoc) {
        useHudStore.getState().hydrateWorkbenchDoc(serverDoc);
        lastCommittedJson = JSON.stringify(buildWorkbenchDoc(useHudStore.getState()));
      } else {
        lastCommittedJson = '';
      }
      return;
    }
    if (asRecord !== undefined) {
      lastCommittedJson = json;
      // W5：向同会话其它 tab 广播已提交真相（CAS revision 随行；revision
      // 缺失时省略广播 —— 发送必被对端丢弃的消息没有意义）。
      const revision = asRecord.mutation_revision;
      if (typeof revision === 'number') {
        collabBroadcastDoc(doc, revision);
      }
    }
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
 * W5：接收其它 tab 广播的已提交 doc —— 水合 + 对齐本地基线与 revision
 * （不回声提交：远端 doc 就是服务器已提交真相）。注意：800ms 防抖窗口内
 * 的本地未提交编辑会被远端 doc 覆盖（全量 LWW 的固有代价 —— CAS 在服务端
 * 裁决，本模块只传播已提交事实）。
 */
function adoptRemoteDoc(doc: WorkbenchDocV5, revision: number): void {
  useHudStore.getState().hydrateWorkbenchDoc(doc);
  lastCommittedJson = JSON.stringify(buildWorkbenchDoc(useHudStore.getState()));
  if (revision >= 0) setMapSpecRevision(revision);
}

// 协同适配器绑定（模块加载一次；persistence ↔ collab 单向依赖）。
// hello 应答只回「已提交基线」—— 防抖窗口内未落盘的本地编辑不得冒充
// 已提交真相被晚到 tab 吸收为基线（R1-m7）。
bindCollabAdapters({
  adoptDoc: adoptRemoteDoc,
  currentDoc: () => {
    if (!armed || lastCommittedJson === '') return null;
    try {
      return {
        doc: JSON.parse(lastCommittedJson) as WorkbenchDocV5,
        revision: getMapSpecSessionCursor().revision,
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
  inflight = false;
  dirtyAfterInflight = false;
  oversizeToastShown = false;
  stopWorkbenchCollab();
}

/** 测试断言辅助：当前防抖基线。 */
export function workbenchPersistenceArmed(): boolean {
  return armed;
}

/**
 * W11：页面卸载（pagehide）前尽力冲刷未落盘的 doc 变更 —— 防抖窗口
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
