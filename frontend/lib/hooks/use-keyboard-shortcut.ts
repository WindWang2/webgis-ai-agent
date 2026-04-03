'use client';
import { useEffect, useCallback } from 'react';
/**
 * Keyboard Shortcuts Hook
 * T005-022: Ctrl+Enter发送、Escape清空输入
 */
interface UseKeyboardShortcutsOptions {
  /** 回车发送(不含Shift) */
  onSend?: () => void;
  /** Escape清空 */
  onClear?: () => void;
  /** Ctrl+S 保存 */
  onSave?: () => void;
  /** 禁用快捷键 */
  disabled?: boolean;
}
/**
 * 键盘快捷键Hook
 */
export function useKeyboardShortcut(options: UseKeyboardShortcutOptions = {}) {
  const { onSend, onClear, onSave, disabled = false } = options;

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (disabled) return;

    // Ctrl+Enter 或 Cmd+Enter 发送
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      onSend?.();
      return;
    }

    // Escape 清空
    if (e.key === 'Escape') {
      e.preventDefault();
      onClear?.();
      return;
    }

    // Ctrl+S / Cmd+S 保存
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      onSave?.();
      return;
    }
  }, [onSend, onClear, onSave, disabled]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);
}

export default useKeyboardShortcut;