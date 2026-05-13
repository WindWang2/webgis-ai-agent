'use client';

import { useHudStore } from '@/lib/store/useHudStore';

export function CausalTrace() {
  const causalChain = useHudStore((s) => s.causalChain);

  if (causalChain.length === 0) return null;

  return (
    <div className='space-y-2'>
      {causalChain.map((entry, idx) => (
        <div key={entry.id} className='flex gap-2'>
          {/* Step number */}
          <div className='flex flex-col items-center'>
            <div className='w-5 h-5 rounded-full bg-violet-100 text-violet-700 flex items-center justify-center text-[10px] font-bold'>
              {causalChain.length - idx}
            </div>
            {idx < causalChain.length - 1 && (
              <div className='w-0.5 flex-1 bg-violet-200 my-1' />
            )}
          </div>

          {/* Content */}
          <div className='flex-1 pb-2'>
            <div className='flex items-center gap-2 mb-1'>
              <span className='text-[10px] font-mono bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded'>
                {entry.tool}
              </span>
              {entry.mapAction && (
                <span className='text-[10px] font-mono bg-green-100 text-green-700 px-1.5 py-0.5 rounded'>
                  {entry.mapAction}
                </span>
              )}
              <span className='text-[10px] text-slate-300 ml-auto font-mono'>
                {entry.time}
              </span>
            </div>
            {entry.toolInput && (
              <div className='text-[10px] text-slate-500 mb-1'>
                <span className='text-slate-400'>输入: </span>
                <code className='font-mono bg-slate-100 px-1 rounded'>{entry.toolInput}</code>
              </div>
            )}
            {entry.mapEffect && (
              <div className='text-[10px] text-slate-600'>
                {entry.mapEffect}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export default CausalTrace;
