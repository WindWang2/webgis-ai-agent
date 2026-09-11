'use client';

import { useEffect, useRef } from 'react';
import type { CommandDef, ShortcutConflict } from './types';
import { normalizeShortcut } from './shortcut';

/**
 * 命令注册表（ADR-0147）。
 *
 * 模块级外部 store（模式同 lib/workbench/undo.ts）：zustand 之外的 plain
 * Map + version + listener Set，React 侧用 useSyncExternalStore 订阅。
 * 注册 append-only：registerCommands 返回反注册函数；重复 id 注册视为
 * 编程错误，devOnly 告警并忽略后者（多线并发不互踩）。
 */

const registry = new Map<string, CommandDef>();
let version = 0;
const listeners = new Set<() => void>();

function emit(): void {
  version++;
  listeners.forEach((l) => l());
}

/** 批量注册，返回反注册函数（幂等）。 */
export function registerCommands(defs: CommandDef[]): () => void {
  const added = new Map<string, CommandDef>();
  for (const def of defs) {
    if (registry.has(def.id)) {
      // append-only 契约：已占用的 id 不被后注册者覆盖。
      if (process.env.NODE_ENV !== 'production') {
        console.warn(`[command-registry] 重复注册被忽略: ${def.id}`);
      }
      continue;
    }
    registry.set(def.id, def);
    added.set(def.id, def);
  }
  if (added.size) emit();
  let done = false;
  return () => {
    if (done) return;
    done = true;
    for (const [id, def] of added) {
      if (registry.get(id) === def) registry.delete(id);
    }
    emit();
  };
}

export function getCommandById(id: string): CommandDef | undefined {
  return registry.get(id);
}

/** 全量命令（按 group 名稳定分组，组内按注册序）。 */
export function getAllCommands(): readonly CommandDef[] {
  return [...registry.values()];
}

export function subscribeCommands(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** useSyncExternalStore 的快照钩子：version 变化才重建数组（getSnapshot 必须缓存）。 */
let snapshotCache: readonly CommandDef[] = [];
let snapshotVersion = -1;
export function getCommandsSnapshot(): readonly CommandDef[] {
  if (snapshotVersion !== version) {
    snapshotCache = [...registry.values()];
    snapshotVersion = version;
  }
  return snapshotCache;
}

export function getCommandsVersion(): number {
  return version;
}

/**
 * 快捷键冲突检测：归一化后同一快捷键被 ≥2 条命令占用。
 * 输入为原始全量（不做 when 过滤）——由调用方决定口径（总览面板对
 * 可见命令检测，框架级检测保持全量）。 */
export function detectShortcutConflicts(commands?: readonly CommandDef[]): ShortcutConflict[] {
  const list = commands ?? getAllCommands();
  const byShortcut = new Map<string, string[]>();
  for (const cmd of list) {
    if (!cmd.shortcut) continue;
    const key = normalizeShortcut(cmd.shortcut);
    const ids = byShortcut.get(key);
    if (ids) ids.push(cmd.id);
    else byShortcut.set(key, [cmd.id]);
  }
  const conflicts: ShortcutConflict[] = [];
  for (const [shortcut, ids] of byShortcut) {
    if (ids.length > 1) conflicts.push({ shortcut, commandIds: ids });
  }
  return conflicts;
}

/** 最近使用记忆（面板顶部优先展示），LRU 上限 8，localStorage 持久化。 */
const RECENTS_KEY = 'geoagent-command-recents';
const RECENTS_MAX = 8;

export function getRecentCommandIds(): string[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const ids: unknown = JSON.parse(raw);
    if (!Array.isArray(ids)) return [];
    return ids.filter((id): id is string => typeof id === 'string').slice(0, RECENTS_MAX);
  } catch {
    return [];
  }
}

export function recordRecentCommand(id: string): void {
  if (typeof localStorage === 'undefined') return;
  const next = [id, ...getRecentCommandIds().filter((x) => x !== id)].slice(0, RECENTS_MAX);
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    /* 存储满/隐私模式：记忆降级为会话内不持久 */
  }
}

/** 测试隔离用：清空注册表与订阅者。生产禁止调用。 */
export function resetRegistryForTests(): void {
  registry.clear();
  version++;
  listeners.clear();
}

/**
 * React 便捷钩子：挂载期注册、卸载期反注册。deps 变化即重注册（先卸旧再
 * 挂新）；defs 每次渲染字面重建是允许的，注册表以 ref 持有最新值。
 */
export function useRegisterCommands(defs: CommandDef[], deps: unknown[] = []): void {
  const ref = useRef(defs);
  ref.current = defs;
  // eslint-disable-next-line react-hooks/exhaustive-deps -- deps 由调用方显式声明
  useEffect(() => registerCommands(ref.current), deps);
}
