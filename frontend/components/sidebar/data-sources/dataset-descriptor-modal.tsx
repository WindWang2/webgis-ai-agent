'use client';

import { useRef } from 'react';
import { X } from 'lucide-react';
import type { DatasetDescriptor } from '@/lib/api/data-fabric';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { IconButton } from '@/components/shared/icon-button';

export interface DatasetDescriptorModalProps {
  descriptor: DatasetDescriptor;
  onClose: () => void;
}

/** DatasetDescriptor 契约弹窗：role=dialog + aria-modal + Escape + 焦点围栏/归还（共用 hook）。 */
export function DatasetDescriptorModal({ descriptor, onClose }: DatasetDescriptorModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus({ open: true, containerRef: dialogRef, onEscape: onClose });

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="DatasetDescriptor 契约"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
    >
      <div className="max-h-[80vh] w-full max-w-md space-y-3 overflow-y-auto rounded-2xl bg-[var(--theme-bg-panel)] p-4 shadow-xl">
        <div className="flex items-center justify-between border-b border-[var(--theme-border)] pb-2">
          <h3 className="text-[14px] font-bold text-[var(--theme-text-primary)]">DatasetDescriptor 契约</h3>
          <IconButton label="关闭" icon={X} onClick={onClose} />
        </div>
        <div className="space-y-2 text-[12px] text-[var(--theme-text-secondary)]">
          <div>
            <span className="font-semibold text-[var(--theme-text-primary)]">ID:</span> {descriptor.id}
          </div>
          <div>
            <span className="font-semibold text-[var(--theme-text-primary)]">标题:</span> {descriptor.title}
          </div>
          <div>
            <span className="font-semibold text-[var(--theme-text-primary)]">几何类型:</span> {descriptor.geometry_type}
          </div>
          <div>
            <span className="font-semibold text-[var(--theme-text-primary)]">SRS 坐标系:</span> {descriptor.srs}
          </div>
          <div>
            <span className="font-semibold text-[var(--theme-text-primary)]">Bounding Box:</span>{' '}
            {JSON.stringify(descriptor.bbox)}
          </div>
          <div>
            <span className="font-semibold text-[var(--theme-text-primary)]">
              字段 Schema ({descriptor.fields?.length || 0}):
            </span>
            <div className="mt-1 max-h-40 overflow-y-auto rounded bg-[var(--theme-bg-muted)] p-2 font-mono text-[11px]">
              {descriptor.fields?.map((f, i) => (
                <div key={i}>
                  {f.name}: <span style={{ color: 'var(--agent-accent, #16a34a)' }}>{f.type}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
