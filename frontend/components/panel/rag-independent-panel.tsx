'use client';

import { X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

export function RagIndependentPanel({ open, onClose }: RagIndependentPanelProps) {
  const ragResults = useHudStore((s) => s.ragResults);

  return (
    <div
      style={{
        position: 'absolute',
        right: 10,
        bottom: 40,
        zIndex: 40,
        width: 380,
        maxHeight: 320,
        background: 'rgba(252,253,254,0.96)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.95)',
        boxShadow: '0 8px 32px rgba(15,23,42,0.12)',
        borderRadius: 16,
        overflow: 'hidden',
        transform: open ? 'translateY(0)' : 'translateY(105%)',
        transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        pointerEvents: open ? 'auto' : 'none',
        opacity: open ? 1 : 0,
      }}
    >
      {/* Header */}
      <div className='flex items-center justify-between px-4 py-3 border-b border-slate-200/60 bg-white/40'>
        <div className='flex items-center gap-2'>
          <div className='w-6 h-6 rounded-lg bg-green-100 flex items-center justify-center'>
            <svg width='14' height='14' viewBox='0 0 14 14' fill='none'>
              <path d='M3 7h8M7 3v8' stroke='#16a34a' strokeWidth='1.5' strokeLinecap='round'/>
              <circle cx='7' cy='7' r='5' stroke='#16a34a' strokeWidth='1'/>
            </svg>
          </div>
          <div>
            <div className='text-xs font-semibold text-slate-800'>RAG 检索</div>
            <div className='text-[14px] text-slate-400'>{ragResults.length} 个结果</div>
          </div>
        </div>
        <button
          onClick={onClose}
          className='w-6 h-6 flex items-center justify-center rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600'
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className='overflow-y-auto max-h-[240px] p-3'>
        {ragResults.length === 0 ? (
          <div className='text-center py-8 text-xs text-slate-400'>
            暂无检索结果
          </div>
        ) : (
          <div className='space-y-2'>
            {ragResults.map((result) => (
              <div
                key={result.id}
                className='p-3 rounded-xl border border-slate-200/60 bg-white/60'
              >
                {/* Source header */}
                <div className='flex items-center justify-between mb-2'>
                  <div className='text-xs font-medium text-slate-700 truncate flex-1'>
                    {result.source}
                  </div>
                  <div className='flex items-center gap-2 ml-2'>
                    <span className='text-[14px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-mono font-semibold'>
                      {result.score}
                    </span>
                    <span className='text-[14px] text-slate-400 font-mono'>
                      {result.chunks} 块
                    </span>
                  </div>
                </div>

                {/* Excerpts */}
                <div className='space-y-1.5'>
                  {result.excerpts.map((excerpt, idx) => (
                    <div
                      key={idx}
                      className='text-[15px] text-slate-500 leading-relaxed'
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
