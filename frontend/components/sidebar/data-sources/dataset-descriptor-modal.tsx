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
    /* D: 去掉 backdrop-blur —— 弹窗盖在地图上，blur 是持续重绘画布上
       最贵的那类合成；scrim（bg-surface-scrim）已承担压暗职责。 */
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="DatasetDescriptor 契约"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-surface-scrim p-4"
    >
        <div className="max-h-[80vh] w-full max-w-md space-y-3 overflow-y-auto rounded-md bg-surface-panel p-4 shadow-overlay">
          <div className="flex items-center justify-between border-b border-edge-subtle pb-2">
            <h3 className="text-title font-bold text-ink">DatasetDescriptor 契约</h3>
            <IconButton label="关闭" icon={X} onClick={onClose} />
          </div>
          <div className="space-y-2 text-meta text-ink-secondary">
            <div>
              <span className="font-semibold text-ink">ID:</span> {descriptor.id}
            </div>
            <div>
              <span className="font-semibold text-ink">标题:</span> {descriptor.title}
            </div>
            <div>
              <span className="font-semibold text-ink">几何类型:</span> {descriptor.geometry_type}
            </div>
            <div>
              <span className="font-semibold text-ink">SRS 坐标系:</span> {descriptor.srs}
            </div>
            <div>
              <span className="font-semibold text-ink">Bounding Box:</span>{' '}
              {JSON.stringify(descriptor.bbox)}
            </div>
            <div>
              <span className="font-semibold text-ink">
                字段 Schema ({descriptor.fields?.length || 0}):
              </span>
              <div className="mt-1 max-h-40 overflow-y-auto rounded-sm bg-surface-sunken p-2 font-mono text-caption">
                {descriptor.fields?.map((f, i) => (
                  <div key={i}>
                    {f.name}: {/* 字段类型是 accent 作文字 —— 用 text-safe 变体。 */}
                    <span className="text-agent-accent">{f.type}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
  );
}
