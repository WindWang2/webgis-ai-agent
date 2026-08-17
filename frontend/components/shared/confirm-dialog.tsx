'use client';

import { useRef } from 'react';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * ConfirmDialog — 模态确认对话框（#553）。
 *
 * 与 ConfirmAction（行内两段式按钮）互补：需要解释说明（如"新建会话将清空
 * 工作区"）或空间不足以做行内两段式的场景用模态。焦点管理复用 useDialogFocus
 * （初始聚焦 / Escape / Tab 围栏 / 关闭回焦），与全站 dialog 行为一致。
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus({ open, containerRef: dialogRef, onEscape: onCancel });

  if (!open) return null;

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      tabIndex={-1}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-surface-scrim p-4"
    >
      <div className="w-full max-w-sm space-y-4 rounded-md border border-edge-subtle bg-surface-overlay p-5 shadow-overlay">
        <h2 id="confirm-dialog-title" className="text-body font-semibold text-ink">
          {title}
        </h2>
        {description && <p className="text-meta text-ink-secondary">{description}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-sm border border-edge-subtle px-3 py-1.5 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="flex items-center gap-1 rounded-sm px-3 py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90"
            style={{ backgroundColor: 'var(--agent-accent)' }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ConfirmDialog;
