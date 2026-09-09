'use client';
/**
 * Undo/redo React 接入（Workbench V5 / W4）。
 *
 * - useUndoRedo：useSyncExternalStore 订阅栈版本（canUndo/canRedo 响应式）；
 * - useWorkbenchUndoKeys：全局快捷键（Ctrl/⌘+Z 撤销、Ctrl/⌘+Shift+Z 或
 *   Ctrl+Y 重做）。输入控件内不拦截（文本编辑的原生 undo 优先）。
 */
import { useEffect, useSyncExternalStore } from 'react';
import {
  canRedo,
  canUndo,
  getUndoSnapshot,
  redo,
  subscribeUndo,
  undo,
} from './undo';

export function useUndoRedo(): {
  canUndo: boolean;
  canRedo: boolean;
  undo: () => void;
  redo: () => void;
} {
  const version = useSyncExternalStore(subscribeUndo, getUndoSnapshot);
  void version;
  return {
    canUndo: canUndo(),
    canRedo: canRedo(),
    undo: () => {
      undo();
    },
    redo: () => {
      redo();
    },
  };
}

function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === 'INPUT'
    || tag === 'TEXTAREA'
    || tag === 'SELECT'
    || el.isContentEditable === true
  );
}

/** 全局 undo/redo 快捷键（workspace 挂载一次）。 */
export function useWorkbenchUndoKeys(): void {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (isEditableTarget(e.target)) return;
      const key = e.key.toLowerCase();
      if (key === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (key === 'y') {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
