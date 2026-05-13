'use client';

import { useHudStore } from '@/lib/store/useHudStore';

export function RagTab() {
  const ragResults = useHudStore((s) => s.ragResults);

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-3 py-2 border-b border-slate-200/60'>
        <span className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider'>
          RAG检索结果
        </span>
      </div>

      {/* Results list */}
      <div className='flex-1 overflow-y-auto p-2'>
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
                    <span className='text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-mono font-semibold'>
                      {result.score}
                    </span>
                    <span className='text-[10px] text-slate-400 font-mono'>
                      {result.chunks} 块
                    </span>
                  </div>
                </div>

                {/* Excerpts */}
                <div className='space-y-1.5'>
                  {result.excerpts.map((excerpt, idx) => (
                    <div
                      key={idx}
                      className='text-[11px] text-slate-500 leading-relaxed'
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

export default RagTab;
