/**
 * Workbench undo/redo（Workbench V5 / W4）—— 用户与 agent 突变的命令模型。
 *
 * 设计（对 async / agent 突变安全）：
 * - 命令在**执行前**捕获 forward/inverse 载荷（快照），执行本身走既有
 *   CAS 串行链 —— undo/redo 不是「状态回写」而是「反向重放同一通道」，
 *   与并发 agent/服务端收敛天然兼容（409 superseded 由既有路径收敛）。
 * - 有界历史（50）；命令带 sessionId，会话切换即清空（clearUndoHistory）。
 * - async 完成不回写历史：undo 执行 = 发起反向提交（fire-and-forget 语义
 *   与正向一致），不持有任何锁，绝不产生半提交状态。
 * - 每个 record 同步写入 opsLog journal（who/what/when + reversible 元数据
 *   —— W4 修复「opLog 零生产者」断供）。
 */
import { useHudStore } from '@/lib/store/useHudStore';
import type { OpLogEntry } from '@/lib/store/hud-types';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { buildWorkbenchDoc } from '@/lib/store/slices/workbenchSlice';
import type { WorkbenchDocV5 } from './doc';
import {
  applyWorkbenchDelta,
  diffWorkbenchDocs,
  invertWorkbenchDelta,
  type WorkbenchDelta,
} from './delta';
import { devOnly } from '@/lib/utils/logger';

export const UNDO_MAX_HISTORY = 50;

/** 可逆命令：undo/redo 重放函数（幂等发起，不持有锁）。 */
export interface WorkbenchCommand {
  id: string;
  label: string;
  kind: OpLogEntry['type'];
  ts: number;
  actor: 'user' | 'agent';
  /** 命令绑定的会话（会话切换清栈的依据）。 */
  sessionId: string | null;
  layerIds?: string[];
  /** 执行反向/正向重放。 */
  undo: () => void;
  redo: () => void;
}

let undoStack: WorkbenchCommand[] = [];
let redoStack: WorkbenchCommand[] = [];
let seq = 0;

// 轻量通知（useUndoRedo 经 useSyncExternalStore 订阅；不进 zustand 防
// 循环依赖：undo.ts 在 commit 后于重放时被 store action 调用）。
let version = 0;
const listeners = new Set<() => void>();
function emitChange(): void {
  version += 1;
  for (const fn of listeners) fn();
}

export function subscribeUndo(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getUndoSnapshot(): number {
  return version;
}

function journal(entry: Pick<OpLogEntry, 'type' | 'label'> & { detail?: string; actor: 'user' | 'agent'; reversible: boolean }): void {
  seq += 1;
  try {
    useHudStore.getState().pushOpLog({
      id: `op-${seq}`,
      time: new Date().toLocaleTimeString('zh-CN'),
      ...entry,
    });
  } catch {
    // journal 断供不得影响命令执行
  }
}

function currentSessionId(): string | null {
  try {
    return getMapSpecSessionCursor().sessionId ?? null;
  } catch {
    return null;
  }
}

/** 命令入栈（正向执行已完成或即将完成 —— 载荷已定，async 完成不回写）。 */
export function recordCommand(cmd: Omit<WorkbenchCommand, 'id' | 'ts' | 'sessionId'>): void {
  undoStack.push({
    ...cmd,
    id: `cmd-${++seq}`,
    ts: Date.now(),
    sessionId: currentSessionId(),
  });
  if (undoStack.length > UNDO_MAX_HISTORY) {
    undoStack = undoStack.slice(undoStack.length - UNDO_MAX_HISTORY);
  }
  redoStack = [];
  journal({
    type: cmd.kind,
    label: cmd.label,
    detail: cmd.layerIds?.length ? `图层: ${cmd.layerIds.slice(0, 3).join(', ')}${cmd.layerIds.length > 3 ? ' …' : ''}` : undefined,
    actor: cmd.actor,
    reversible: true,
  });
  emitChange();
}

/** 不可逆操作仅入 journal（如 remove_layer 落账）—— 不进 undo 栈。 */
export function journalOnly(entry: {
  type: OpLogEntry['type'];
  label: string;
  detail?: string;
  actor: 'user' | 'agent';
}): void {
  journal({ ...entry, reversible: false });
}

export function canUndo(): boolean {
  return undoStack.length > 0;
}

export function canRedo(): boolean {
  return redoStack.length > 0;
}

/** 撤销栈顶：执行其 undo 重放（旧会话命令已被清栈，不可能跨会话撤销）。 */
export function undo(): boolean {
  const cmd = undoStack.pop();
  if (!cmd) return false;
  try {
    cmd.undo();
  } finally {
    redoStack.push(cmd);
    journal({
      type: 'undo',
      label: `撤销：${cmd.label}`,
      actor: 'user',
      reversible: true,
    });
  }
  emitChange();
  return true;
}

export function redo(): boolean {
  const cmd = redoStack.pop();
  if (!cmd) return false;
  try {
    cmd.redo();
  } finally {
    undoStack.push(cmd);
    journal({
      type: 'redo',
      label: `重做：${cmd.label}`,
      actor: 'user',
      reversible: true,
    });
  }
  emitChange();
  return true;
}

/** 会话切换 / 恢复：清空两栈（跨会话命令不可撤销 —— 图层 id 语义已变）。 */
export function clearUndoHistory(): void {
  const had = undoStack.length + redoStack.length;
  undoStack = [];
  redoStack = [];
  if (had > 0) emitChange();
}

/* ─── 命令构造器（capture-before-execute）─────────────────────────────── */

/**
 * 组织态命令（分组树/成员/锁）：undo/redo = **inverse/forward delta 重放**
 * （V6 / R1-M3+M5）。V5 的整表快照回写在并发下会覆盖他人在窗口期的编辑
 * （no whole-table rollback 违例）；delta 反演只回退本命令触碰的字段，
 * 与远端提交经同一 CAS 串行链收敛。
 * R1-M2：不含 mode —— 模式切换有自己的协调入口且不应被组织态撤销静默回退。
 */
export function docCommand(
  label: string,
  actor: 'user' | 'agent',
  before: WorkbenchDocV5,
  after: WorkbenchDocV5,
): void {
  const forwardDelta = diffWorkbenchDocs(before, after);
  if (forwardDelta == null) return; // 无组织态差异（mode-only）→ 不入栈
  let inverseDelta: WorkbenchDelta;
  try {
    inverseDelta = invertWorkbenchDelta(forwardDelta, before);
  } catch {
    // 反演不可构造（理论不应发生：delta 域闭包）→ journal-only 并披露。
    journalOnly({ type: 'group', label, actor, detail: 'irreversible (invert failed)' });
    return;
  }
  recordCommand({
    label,
    kind: 'group',
    actor,
    layerIds: [],
    undo: () => void replayWorkbenchDelta(inverseDelta, label),
    redo: () => void replayWorkbenchDelta(forwardDelta, label),
  });
}

/** delta 重放：应用到当前 store 组织态 → 既有持久化订阅自动经 CAS 通道提交。 */
function replayWorkbenchDelta(delta: WorkbenchDelta, label: string): void {
  try {
    const current = buildWorkbenchDoc(useHudStore.getState());
    const next = applyWorkbenchDelta(current, delta);
    useHudStore.setState({
      layerGroups: next.groups.map((g) => ({ ...g })),
      layerGroupMembership: { ...next.membership },
      lockedLayerIds: [...next.lockedLayerIds],
    });
  } catch (err) {
    // 重放非法（并发下引用漂移）：journal 留痕，不产生半更新。
    devOnly.warn('[undo] delta replay skipped:', label, err);
  }
}

function currentDoc(): WorkbenchDocV5 {
  return buildWorkbenchDoc(useHudStore.getState());
}

/** 一步式组织态命令包装：捕获 → 执行 → 记录（组件调用点最小化）。 */
export function withDocUndo(label: string, actor: 'user' | 'agent', mutate: () => void): void {
  const before = currentDoc();
  mutate();
  const after = currentDoc();
  // R1-M2：比较与回放均不含 mode（组织态撤销只管组织态）。
  const orgSlice = (d: WorkbenchDocV5) =>
    JSON.stringify({ g: d.groups, m: d.membership, l: d.lockedLayerIds });
  if (orgSlice(before) === orgSlice(after)) return;
  docCommand(label, actor, before, after);
}

/** presentation 命令（显隐/不透明度）：inverse = 同通道反向提交。 */
export function presentationCommand(
  label: string,
  layerId: string,
  actor: 'user' | 'agent',
  before: { visible?: boolean; opacity?: number },
  after: { visible?: boolean; opacity?: number },
): void {
  recordCommand({
    label,
    kind: 'toggle',
    actor,
    layerIds: [layerId],
    undo: () => void commitPresentation(layerId, before),
    redo: () => void commitPresentation(layerId, after),
  });
}

function commitPresentation(layerId: string, patch: { visible?: boolean; opacity?: number }): void {
  loadUserMutation()
    .then(({ commitLayerPresentation }) => commitLayerPresentation({ layerId, ...patch }))
    .catch((err) => {
      // 反向提交失败保持现状（CAS 真相在服务端）；journal 已留痕。
      devOnly.warn('[undo] presentation replay failed:', err);
    });
}

// 共享单例 import promise：连续 undo→redo 会并发发起同一模块的动态 import，
// 部分打包器/mock 注册表下第二个并发 import 的 promise 永不 settle（重放
// 静默丢失）。首解析后全部重放共享同一 promise —— 无并发重复 import。
type UserMutationModule = typeof import('@/lib/mapspec/user-mutation');
let userMutationPromise: Promise<UserMutationModule> | null = null;
function loadUserMutation(): Promise<UserMutationModule> {
  if (!userMutationPromise) {
    userMutationPromise = import('@/lib/mapspec/user-mutation');
  }
  return userMutationPromise;
}

/**
 * reorder 命令：inverse = 裸提交（store 重排 + commitMapSpecMutation）。
 * R1-M1：重放**不得**经过 reorderLayersAndCommit —— 该函数会再次
 * recordCommand（清空 redo 栈 + 污染 undo 栈，redo 一次需 undo 两次）。
 * 裸路径只做正向提交，不进 undo 记账。
 */
export function reorderCommand(
  label: string,
  actor: 'user' | 'agent',
  beforeOrder: { id: string; _mapspecLayerId?: string }[],
  afterOrder: { id: string; _mapspecLayerId?: string }[],
): void {
  recordCommand({
    label,
    kind: 'reorder',
    actor,
    layerIds: beforeOrder.slice(0, 5).map((l) => l.id),
    undo: () => void replayReorder(beforeOrder),
    redo: () => void replayReorder(afterOrder),
  });
}

function replayReorder(order: { id: string; _mapspecLayerId?: string }[]): void {
  Promise.all([
    useHudStore.getState().reorderLayers(order as never),
    loadUserMutation().then(({ commitMapSpecMutation }) =>
      commitMapSpecMutation({
        intent: 'reorder_layers',
        layer_ids: order.map((layer) => String(layer._mapspecLayerId || layer.id)),
      }),
    ),
  ]).catch((err) => devOnly.warn('[undo] reorder replay failed:', err));
}

/** 测试隔离。 */
export function resetUndoForTests(): void {
  undoStack = [];
  redoStack = [];
  seq = 0;
  emitChange();
}
