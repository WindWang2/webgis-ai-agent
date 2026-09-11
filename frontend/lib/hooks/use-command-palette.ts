'use client';

import { useEffect } from 'react';
import { create } from 'zustand';
import { getAllCommands, getCommandById, recordRecentCommand } from '@/lib/commands/registry';
import { isEditableTarget, matchesShortcut, normalizeShortcut } from '@/lib/commands/shortcut';
import type { CommandExecutionContext } from '@/lib/commands/types';

/**
 * 命令面板 / 快捷键总览 共享开关 store（ADR-0147）。
 *
 * 独立小 store，不进 useHudStore——hud partialize 白名单与 rail 注册语义
 * 是多线共享面（§8 禁改），面板开关纯属本线 UI 状态。
 */

export type CommandSurface = 'palette' | 'shortcuts' | null;

interface CommandPaletteState {
  surface: CommandSurface;
  open: (surface: Exclude<CommandSurface, null>) => void;
  close: () => void;
  toggle: (surface: Exclude<CommandSurface, null>) => void;
}

export const useCommandPaletteStore = create<CommandPaletteState>((set, get) => ({
  surface: null,
  open: (surface) => set({ surface }),
  close: () => set({ surface: null }),
  toggle: (surface) => set({ surface: get().surface === surface ? null : surface }),
}));

/** 命中并执行一条已注册命令。快捷键路径在调用前完成 when 判定。 */
export async function runCommandById(
  id: string,
  ctx: CommandExecutionContext = { source: 'api' },
  input?: string,
): Promise<void> {
  const def = getCommandById(id);
  if (!def) return;
  if (def.when && !def.when()) return;
  await def.run(ctx, input);
  recordRecentCommand(id);
}

/**
 * 全局热键（挂载一次）：
 * - Ctrl/Cmd+K：命令面板开关（可编辑焦点内也生效——搜索框场景是主入口）。
 * - `?`：快捷键总览（仅非可编辑焦点，避免打字误触）。
 * 面板内部 Escape/Tab 由 useDialogFocus 与面板自身处理，这里不抢。
 */
export function useCommandPaletteHotkeys(): void {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const store = useCommandPaletteStore.getState();
      if (matchesShortcut(e, normalizeShortcut('mod+k'))) {
        e.preventDefault();
        e.stopPropagation();
        store.toggle('palette');
        return;
      }
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (isEditableTarget(e.target)) return;
        e.preventDefault();
        store.toggle('shortcuts');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}

/** 面板打开时锁定背景滚动。 */
export function useCommandPaletteScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [active]);
}

export { getAllCommands };
