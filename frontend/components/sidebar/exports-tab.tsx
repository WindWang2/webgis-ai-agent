'use client';

import { Download, Trash2 } from 'lucide-react';
import { useHudStore, DEMO_EXPORTS } from '@/lib/store/useHudStore';

const iconForType: Record<string, string> = {
  png: '🖼',
  pdf: '📄',
  geojson: '📍',
};

export function ExportsTab() {
  const exports = useHudStore((s) => s.exports);
  const setExports = useHudStore((s) => s.setExports);
  const demoMode = useHudStore((s) => s.demoMode);
  const setDemoMode = useHudStore((s) => s.setDemoMode);
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';

  const displayExports = demoMode && exports.length === 0 ? DEMO_EXPORTS : exports;

  const handleDownload = (item: any) => {
    // Mock download
    alert(`下载 ${item.name}`);
  };

  const handleDelete = (id: string) => {
    setExports(exports.filter(e => e.id !== id));
  };

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-3 py-2' style={{ borderBottomColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.6)', borderBottomWidth: 1, borderBottomStyle: 'solid' }}>
        <span className='text-[10px] font-semibold uppercase tracking-wider' style={{ color: isDark ? '#64748b' : '#94a3b8' }}>
          导出文件
        </span>
        <div className='flex items-center gap-1'>
          {!demoMode && exports.length === 0 && (
            <button
              onClick={() => setDemoMode(true)}
              className='text-[10px] px-2 py-1 rounded hover:bg-opacity-50'
              style={{ color: isDark ? '#64748b' : '#94a3b8', backgroundColor: 'transparent' }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
            >
              加载演示
            </button>
          )}
          {displayExports.length > 0 && (
            <button
              onClick={() => setExports([])}
              className='text-[10px] px-2 py-1 rounded hover:bg-opacity-50'
              style={{ color: isDark ? '#fca5a5' : '#ef4444', backgroundColor: 'transparent' }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(248,113,113,0.15)' : 'rgba(254,226,226,0.6)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
            >
              清空
            </button>
          )}
        </div>
      </div>

      {/* Exports list */}
      <div className='flex-1 overflow-y-auto p-2'>
        {displayExports.length === 0 ? (
          <div className='flex flex-col items-center justify-center h-full text-center px-6'>
            <div className='w-10 h-10 rounded-xl flex items-center justify-center mb-2' style={{ backgroundColor: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)' }}>
              <Download size={16} style={{ color: isDark ? '#475569' : '#cbd5e1' }} />
            </div>
            <p className='text-[11.5px]' style={{ color: isDark ? '#64748b' : '#94a3b8' }}>暂无导出文件</p>
          </div>
        ) : (
          <div className='space-y-1'>
            {displayExports.map((item) => (
              <div
                key={item.id}
                className='flex items-center gap-2 p-2 rounded-lg cursor-pointer transition-colors'
                style={{ backgroundColor: 'transparent' }}
                onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.1)' : 'rgba(248,250,252,0.8)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
              >
                <div className='w-8 h-8 rounded-lg flex items-center justify-center text-lg flex-shrink-0' style={{ backgroundColor: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)' }}>
                  {iconForType[item.type] || '📁'}
                </div>
                <div className='flex-1 min-w-0'>
                  <div className='text-xs font-medium truncate' style={{ color: isDark ? '#e2e8f0' : '#334155' }}>
                    {item.name}
                  </div>
                  <div className='flex items-center gap-2 mt-0.5'>
                    <span className='text-[10px] font-mono uppercase' style={{ color: isDark ? '#64748b' : '#94a3b8' }}>
                      {item.type}
                    </span>
                    <span className='text-[10px]' style={{ color: isDark ? '#475569' : '#cbd5e1' }}>•</span>
                    <span className='text-[10px] font-mono' style={{ color: isDark ? '#64748b' : '#94a3b8' }}>
                      {item.size}
                    </span>
                  </div>
                </div>
                <div className='text-[10px] flex-shrink-0' style={{ color: isDark ? '#475569' : '#cbd5e1' }}>
                  {item.date}
                </div>
                <div className='flex items-center gap-0.5 flex-shrink-0'>
                  <button
                    onClick={() => handleDownload(item)}
                    className='p-1 rounded'
                    style={{ color: isDark ? '#64748b' : '#94a3b8', backgroundColor: 'transparent' }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(74,222,128,0.15)' : 'rgba(16,185,129,0.12)'; e.currentTarget.style.color = isDark ? '#4ade80' : '#10b981'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#64748b' : '#94a3b8'; }}
                    title="下载"
                  >
                    <Download size={11} />
                  </button>
                  <button
                    onClick={() => handleDelete(item.id)}
                    className='p-1 rounded'
                    style={{ color: isDark ? '#64748b' : '#94a3b8', backgroundColor: 'transparent' }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(248,113,113,0.15)' : 'rgba(254,226,226,0.6)'; e.currentTarget.style.color = isDark ? '#fca5a5' : '#ef4444'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#64748b' : '#94a3b8'; }}
                    title="删除"
                  >
                    <Trash2 size={11} />
                  </button>
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