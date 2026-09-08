/**
 * Workbench doc 持久化适配器（Workbench V5 / W3）。
 *
 * 组织态（分组树/成员/锁/模式）经既有 MapSpec mutation 通道（CAS 串行链 +
 * session 锁 + provenance）落盘到 `mapspec.workbench` 分支 —— 本模块不建
 * 第二真相（无 localStorage 投影、无独立端点）：
 *   - hydrateWorkbenchFromSpec：恢复路径把 spec.workbench 归一化后水合 store；
 *   - startWorkbenchPersistence：store 订阅 + 防抖提交（armed 门：恢复完成
 *     前绝不提交，防会话切换竞态把空 doc 盖掉新会话的服务器端 doc）。
 */
import { useHudStore } from '@/lib/store/useHudStore';
import { buildWorkbenchDoc } from '@/lib/store/slices/workbenchSlice';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { normalizeWorkbenchDoc, workbenchDocBytes, WORKBENCH_DOC_MAX_BYTES, type WorkbenchDocV5 } from './doc';
import { devOnly } from '@/lib/utils/logger';

const PERSIST_DEBOUNCE_MS = 800;

let armed = false;
let targetSessionId: string | null = null;
let lastCommittedJson = '';
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let inflight = false;
let dirtyAfterInflight = false;
let unsubscribe: (() => void) | null = null;

function currentDocJson(): string {
  const s = useHudStore.getState();
  return JSON.stringify(buildWorkbenchDoc(s));
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
}

/**
 * 恢复完成：以当前 store 快照为已提交基线并武装提交门。spec 无 workbench
 * 分支（旧会话）同样调用 —— 用户此后的组织态编辑从空基线开始持久化。
 */
export function markWorkbenchHydrated(): void {
  lastCommittedJson = currentDocJson();
  armed = true;
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

function scheduleCommit(): void {
  if (!armed || targetSessionId == null) return;
  const json = currentDocJson();
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
  // 64KB 闸的客户端预检（与后端引擎同款上限）：超限拒绝并提示 ——
  // 组织态不携带数据（大载荷属 layers/sources/ref 通道）。
  if (workbenchDocBytes(doc) > WORKBENCH_DOC_MAX_BYTES) {
    devOnly.warn('[workbench-persist] doc exceeds size cap; not committed');
    return;
  }
  inflight = true;
  try {
    const { commitMapSpecMutation } = await import('@/lib/mapspec/user-mutation');
    const result = await commitMapSpecMutation({
      intent: 'patch_workbench_state',
      doc,
    });
    // 提交成功以「本次提交的 json」收敛基线 —— inflight 窗口内的后续编辑
    // （dirtyAfterInflight）由订阅触发下一轮提交，永不丢增量。
    if (result !== undefined) lastCommittedJson = json;
  } catch (err) {
    // 409 superseded 已由 commitMapSpecMutation 收敛（回灌服务端真相 →
    // 订阅再次触发 → 与 lastCommittedJson 不同则重提交）；其它错误保脏，
    // 由下一次组织态编辑或恢复重试驱动，不打断用户。
    devOnly.warn('[workbench-persist] commit failed (kept dirty):', err);
  } finally {
    inflight = false;
    if (dirtyAfterInflight) {
      dirtyAfterInflight = false;
      scheduleCommit();
    }
  }
}

/** 启动 store 订阅（app bootstrap 一次）。重复调用幂等。 */
export function startWorkbenchPersistence(): void {
  if (unsubscribe != null) return;
  let prev = currentDocJson();
  unsubscribe = useHudStore.subscribe((state) => {
    const json = JSON.stringify(buildWorkbenchDoc(state));
    if (json === prev) return;
    prev = json;
    scheduleCommit();
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
}

/** 测试断言辅助：当前防抖基线。 */
export function workbenchPersistenceArmed(): boolean {
  return armed;
}
