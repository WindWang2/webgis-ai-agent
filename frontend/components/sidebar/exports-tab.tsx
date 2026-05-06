'use client';

import { useHudStore, DEMO_EXPORTS } from '@/lib/store/useHudStore';

const iconForType: Record<string, string> = {
  png: '🖼',
  pdf: '📄',
  geojson: '📍',
};

export function ExportsTab() {
  const exports = useHudStore((s) => s.exports);
  const demoMode = useHudStore((s) => s.demoMode);
  const setDemoMode = useHudStore((s) => s.setDemoMode);

  const displayExports = demoMode && exports.length === 0 ? DEMO_EXPORTS : exports;

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-3 py-2 border-b border-slate-200/60'>
        <span className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider'>
          导出文件
        </span>
        {!demoMode && exports.length === 0 && (
          <button
            onClick={() => setDemoMode(true)}
            className='text-[10px] text-slate-400 hover:text-slate-600'
          >
            加载演示
          </button>
        )}
      </div>

      {/* Exports list */}
      <div className='flex-1 overflow-y-auto p-2'>
        {displayExports.length === 0 ? (
          <div className='text-center py-8 text-xs text-slate-400'>
            暂无导出文件
          </div>
        ) : (
          <div className='space-y-1'>
            {displayExports.map((item) => (
              <div
                key={item.id}
                className='flex items-center gap-2 p-2 rounded-lg hover:bg-slate-100/60 cursor-pointer'
              >
                <div className='w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-lg flex-shrink-0'>
                  {iconForType[item.type] || '📁'}
                </div>
                <div className='flex-1 min-w-0'>
                  <div className='text-xs text-slate-700 font-medium truncate'>
                    {item.name}
                  </div>
                  <div className='flex items-center gap-2 mt-0.5'>
                    <span className='text-[10px] text-slate-400 font-mono uppercase'>
                      {item.type}
                    </span>
                    <span className='text-[10px] text-slate-300'>•</span>
                    <span className='text-[10px] text-slate-400 font-mono'>
                      {item.size}
                    </span>
                  </div>
                </div>
                <div className='text-[10px] text-slate-300 flex-shrink-0'>
                  {item.date}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default ExportsTab;