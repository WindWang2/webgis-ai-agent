'use client';

import { useHudStore } from '@/lib/store/useHudStore';

const iconForType: Record<string, string> = {
  add: '+',
  remove: '−',
  toggle: '⇄',
  flyto: '⟶',
  style: '✎',
};

const colorForType: Record<string, string> = {
  add: '#16a34a',
  remove: '#dc2626',
  toggle: '#2563eb',
  flyto: '#7c3aed',
  style: '#ca8a04',
};

export function OpsLogTab() {
  const opsLog = useHudStore((s) => s.opsLog);
  const clearOpsLog = useHudStore((s) => s.clearOpsLog);

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-3 py-2 border-b border-slate-200/60'>
        <span className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider'>
          操作日志
        </span>
        {opsLog.length > 0 && (
          <button
            onClick={clearOpsLog}
            className='text-[10px] text-slate-400 hover:text-red-500 px-2 py-1 rounded hover:bg-red-50'
            title='清空日志'
          >
            清空
          </button>
        )}
      </div>

      {/* Log list */}
      <div className='flex-1 overflow-y-auto p-2'>
        {opsLog.length === 0 ? (
          <div className='text-center py-8 text-xs text-slate-400'>
            暂无操作记录
          </div>
        ) : (
          <div className='space-y-1'>
            {opsLog.map((entry) => (
              <div
                key={entry.id}
                className='flex items-start gap-2 p-2 rounded-lg hover:bg-slate-100/60'>
                <div
                  className='w-6 h-6 rounded flex items-center justify-center text-xs font-bold flex-shrink-0'
                  style={{
                    backgroundColor: colorForType[entry.type] + '20',
                    color: colorForType[entry.type],
                  }}
                >
                  {iconForType[entry.type] || '•'}
                </div>
                <div className='flex-1 min-w-0'>
                  <div className='text-xs text-slate-700 font-medium'>
                    {entry.label}
                  </div>
                  {entry.detail && (
                    <div className='text-[10px] text-slate-400 mt-0.5 font-mono'>
                      {entry.detail}
                    </div>
                  )}
                </div>
                <div className='text-[10px] text-slate-300 font-mono flex-shrink-0'>
                  {entry.time}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default OpsLogTab;
