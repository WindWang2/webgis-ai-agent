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
    /* D: 去掉 backdrop-blur —— 弹窗盖在地图上，blur 是持续重绘画布上
       最贵的那类合成；scrim（bg-surface-scrim）已承担压暗职责。 */
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="数据样例预览"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-surface-scrim p-4"
    >
        <div className="max-h-[80vh] w-full max-w-lg space-y-3 overflow-y-auto rounded-md bg-surface-panel p-4 shadow-overlay">
          <div className="flex items-center justify-between border-b border-edge-subtle pb-2">
            <h3 className="text-title font-bold text-ink">
              数据样例预览 ({result.features.length} 要素)
            </h3>
            <IconButton label="关闭" icon={X} onClick={onClose} />
          </div>
          {/* 主题令牌化（review P2）：bg-slate-900 在暗色下与面板底色融为一体 */}
          {/* JSON 是 accent 作正文 —— 用主题校正后的 --agent-accent。 */}
          <div className="max-h-60 overflow-x-auto rounded-md border border-edge-subtle bg-surface-sunken p-3 font-mono text-caption text-agent-accent">
            <pre>{JSON.stringify(result.features.slice(0, 3), null, 2)}</pre>
          </div>
        </div>
      </div>
  );
}
