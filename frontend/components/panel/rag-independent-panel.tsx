'use client';

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useInertWhenClosed } from '@/lib/hooks/use-inert';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

export function RagIndependentPanel({ open, onClose }: RagIndependentPanelProps) {
  const ragResults = useHudStore((s) => s.ragResults);
  const panelRef = useRef<HTMLDivElement>(null);

  // 该面板始终挂载、仅靠 transform+opacity 视觉隐藏，读屏仍会读到其中的内容；
  // role="dialog" + aria-modal="false" 给出轻量对话框语义。
  // 关闭时同时打 aria-hidden 与 inert：只写 aria-hidden 反而是 ARIA 违规 ——
  // 容器里仍有可聚焦控件，键盘会 Tab 进一个看不见、也不会被播报的面板。
  // inert 一并移除命中测试、焦点与 a11y 树（见 use-inert.ts）。
  useInertWhenClosed(panelRef, open);

  // Escape 只在 open 时注册监听、卸载时清理。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="false"
      aria-labelledby="rag-panel-title"
      aria-hidden={!open}
      style={{
        position: 'absolute',
        right: 10,
        bottom: 40,
        zIndex: 40,
        width: 380,
        maxHeight: 320,
        // V4：overlay 表面不透明（--surface-overlay 双主题均不透明），
        // 半透明白底 + backdrop blur 已无作用，去掉。
        background: 'var(--surface-overlay)',
        border: '1px solid var(--border-subtle)',
        boxShadow: 'var(--elevation-overlay)',
        borderRadius: 16,
        overflow: 'hidden',
        transform: open ? 'translateY(0)' : 'translateY(105%)',
        transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        pointerEvents: open ? 'auto' : 'none',
        opacity: open ? 1 : 0,
      }}
    >
      {/* Header */}
      <div className='flex items-center justify-between px-4 py-3 border-b border-edge-subtle bg-surface-raised'>
        <div className='flex items-center gap-2'>
          <div className='w-6 h-6 rounded-md bg-status-accent-soft flex items-center justify-center'>
            <svg width='14' height='14' viewBox='0 0 14 14' fill='none'>
              <path d='M3 7h8M7 3v8' stroke='var(--accent)' strokeWidth='1.5' strokeLinecap='round'/>
              <circle cx='7' cy='7' r='5' stroke='var(--accent)' strokeWidth='1'/>
            </svg>
          </div>
          <div>
            <div id="rag-panel-title" className='text-meta font-semibold text-ink'>RAG 检索</div>
            <div className='text-body text-ink-muted'>{ragResults.length} 个结果</div>
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="关闭"
          className='w-6 h-6 flex items-center justify-center rounded-sm hover:bg-surface-hover text-ink-disabled hover:text-ink-secondary'
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className='overflow-y-auto max-h-[240px] p-3'>
        {ragResults.length === 0 ? (
          <div className='text-center py-8 text-meta text-ink-muted'>
            暂无检索结果
          </div>
        ) : (
          <div className='space-y-2'>
            {ragResults.map((result) => (
              <div
                key={result.id}
                className='p-3 rounded-md border border-edge-subtle bg-surface-raised'
              >
                {/* Source header */}
                <div className='flex items-center justify-between mb-2'>
                  <div className='text-meta font-medium text-ink-secondary truncate flex-1'>
                    {result.source}
                  </div>
                  <div className='flex items-center gap-2 ml-2'>
                    <span className='text-body px-1.5 py-0.5 rounded-pill bg-status-accent-soft text-status-accent font-mono font-semibold'>
                      {result.score}
                    </span>
                    <span className='text-body text-ink-muted font-mono'>
                      {result.chunks} 块
                    </span>
                  </div>
                </div>

                {/* Excerpts */}
                <div className='space-y-1.5'>
                  {result.excerpts.map((excerpt, idx) => (
                    <div
                      key={idx}
                      className='text-body text-ink-muted leading-relaxed'
                    >
                      &quot;{excerpt}&quot;
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default RagIndependentPanel;
