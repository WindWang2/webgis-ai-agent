'use client';

import { useRef } from 'react';
import { X } from 'lucide-react';
import type { QueryResult } from '@/lib/api/data-fabric';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { IconButton } from '@/components/shared/icon-button';

export interface PreviewModalProps {
  result: QueryResult;
  onClose: () => void;
}

/** 数据样例预览弹窗：role=dialog + aria-modal + Escape + 焦点围栏/归还（共用 hook）。 */
export function PreviewModal({ result, onClose }: PreviewModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus({ open: true, containerRef: dialogRef, onEscape: onClose });

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="数据样例预览"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
    >
      <div className="max-h-[80vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-[var(--theme-bg-panel)] p-4 shadow-xl">
        <div className="flex items-center justify-between border-b border-[var(--theme-border)] pb-2">
          <h3 className="text-[14px] font-bold text-[var(--theme-text-primary)]">
            数据样例预览 ({result.features.length} 要素)
          </h3>
          <IconButton label="关闭" icon={X} onClick={onClose} />
        </div>
        {/* 主题令牌化（review P2）：bg-slate-900 在暗色下与面板底色融为一体 */}
        <div className="max-h-60 overflow-x-auto rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-input)] p-3 font-mono text-[11px] text-[var(--agent-accent,#16a34a)]">
          <pre>{JSON.stringify(result.features.slice(0, 3), null, 2)}</pre>
        </div>
      </div>
    </div>
  );
}
