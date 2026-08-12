'use client';

import { useEffect } from 'react';
import { X } from 'lucide-react';
import type { QueryResult } from '@/lib/api/data-fabric';
import { IconButton } from '@/components/shared/icon-button';

export interface PreviewModalProps {
  result: QueryResult;
  onClose: () => void;
}

/** 数据样例预览弹窗：role=dialog + aria-modal + Escape 关闭。 */
export function PreviewModal({ result, onClose }: PreviewModalProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="数据样例预览"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
    >
      <div className="max-h-[80vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-2xl bg-[var(--theme-bg-panel)] p-4 shadow-xl">
        <div className="flex items-center justify-between border-b border-[var(--theme-border)] pb-2">
          <h3 className="text-[14px] font-bold text-[var(--theme-text-primary)]">
            数据样例预览 ({result.features.length} 要素)
          </h3>
          <IconButton label="关闭" icon={X} onClick={onClose} />
        </div>
        <div className="max-h-60 overflow-x-auto rounded-xl bg-slate-900 p-3 font-mono text-[11px] text-[var(--agent-accent,#16a34a)]">
          <pre>{JSON.stringify(result.features.slice(0, 3), null, 2)}</pre>
        </div>
      </div>
    </div>
  );
}
