'use client';

import { useHudStore } from '@/lib/store/useHudStore';

interface TweaksPanelProps {
  children?: React.ReactNode;
}

const ACCENT_COLORS = ['#16a34a', '#2563eb', '#7c3aed', '#dc2626', '#0891b2'];

export function TweaksPanel({ children }: TweaksPanelProps) {
  const tweaksOpen = useHudStore((s) => s.tweaksOpen);
  const setTweaksOpen = useHudStore((s) => s.setTweaksOpen);
  const accentColor = useHudStore((s) => s.accentColor);
  const setAccentColor = useHudStore((s) => s.setAccentColor);
  const theme = useHudStore((s) => s.theme);
  const setTheme = useHudStore((s) => s.setTheme);
  const fontSize = useHudStore((s) => s.fontSize);
  const setFontSize = useHudStore((s) => s.setFontSize);
  const density = useHudStore((s) => s.density);
  const setDensity = useHudStore((s) => s.setDensity);
  const hudOpen = useHudStore((s) => s.hudOpen);
  const setHudOpen = useHudStore((s) => s.setHudOpen);
  const ragPanelOpen = useHudStore((s) => s.ragPanelOpen);
  const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);
  const showGrid = useHudStore((s) => s.showGrid);
  const setShowGrid = useHudStore((s) => s.setShowGrid);

  return (
    <>
      {/* Tweaks panel */}
      <div
        style={{
          position: 'fixed',
          bottom: 30,
          left: '50%',
          transform: tweaksOpen ? 'translateX(-50%)' : 'translateX(-50%) translateY(105%)',
          zIndex: 100,
          background: 'rgba(252,253,254,0.96)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          border: '1px solid rgba(255,255,255,0.95)',
          boxShadow: '0 8px 32px rgba(15,23,42,0.12)',
          borderRadius: 16,
          padding: 16,
          minWidth: 300,
          transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
          pointerEvents: tweaksOpen ? 'auto' : 'none',
          opacity: tweaksOpen ? 1 : 0,
        }}
      >
        {/* Header */}
        <div className='flex items-center justify-between mb-3'>
          <div className='text-xs font-semibold text-slate-800'>UI 调整</div>
          <button
            onClick={() => setTweaksOpen(false)}
            className='text-[10px] text-slate-400 hover:text-slate-600'
          >
            关闭
          </button>
        </div>

        {/* Accent color */}
        <div className='mb-4'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2'>
            主题色
          </div>
          <div className='flex gap-2'>
            {ACCENT_COLORS.map((color) => (
              <button
                key={color}
                onClick={() => setAccentColor(color)}
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: 6,
                  backgroundColor: color,
                  border: accentColor === color ? '2px solid #0f172a' : '2px solid transparent',
                  cursor: 'pointer',
                }}
              />
            ))}
          </div>
        </div>

        {/* Font size */}
        <div className='mb-4'>
          <div className='flex items-center justify-between mb-2'>
            <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider'>
              字体大小
            </div>
            <span className='text-[10px] text-slate-500 font-mono'>{fontSize}px</span>
          </div>
          <input
            type='range'
            min={11}
            max={16}
            step={0.5}
            value={fontSize}
            onChange={(e) => setFontSize(parseFloat(e.target.value))}
            className='w-full'
          />
        </div>

        {/* Theme */}
        <div className='mb-4'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2'>
            主题
          </div>
          <div className='flex gap-1'>
            {['light', 'dark'].map((t) => (
              <button
                key={t}
                onClick={() => setTheme(t as 'light' | 'dark')}
                style={{
                  flex: 1,
                  padding: '6px 12px',
                  borderRadius: 8,
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 11,
                  background: theme === t ? 'rgba(15,23,42,0.06)' : 'transparent',
                  color: theme === t ? '#0f172a' : '#64748b',
                }}
              >
                {t === 'light' ? '亮色' : '暗色'}
              </button>
            ))}
          </div>
        </div>

        {/* Density */}
        <div className='mb-4'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2'>
            信息密度
          </div>
          <div className='flex gap-1'>
            {['compact', 'comfortable'].map((d) => (
              <button
                key={d}
                onClick={() => setDensity(d as 'compact' | 'comfortable')}
                style={{
                  flex: 1,
                  padding: '6px 12px',
                  borderRadius: 8,
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 11,
                  background: density === d ? 'rgba(15,23,42,0.06)' : 'transparent',
                  color: density === d ? '#0f172a' : '#64748b',
                }}
              >
                {d === 'compact' ? '紧凑' : '舒适'}
              </button>
            ))}
          </div>
        </div>

        {/* Toggles */}
        <div className='space-y-2'>
          <div className='text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2'>
            面板
          </div>

          <ToggleRow
            label='Agent 环境 HUD'
            value={hudOpen}
            onChange={setHudOpen}
          />
          <ToggleRow
            label='RAG 独立面板'
            value={ragPanelOpen}
            onChange={setRagPanelOpen}
          />
          <ToggleRow
            label='显示地图网格'
            value={showGrid}
            onChange={setShowGrid}
          />
        </div>
      </div>

      {children}
    </>
  );
}

function ToggleRow({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className='flex items-center justify-between py-1.5'>
      <span className='text-xs text-slate-600'>{label}</span>
      <button
        onClick={() => onChange(!value)}
        style={{
          width: 36,
          height: 20,
          borderRadius: 10,
          border: 'none',
          cursor: 'pointer',
          transition: 'background 0.2s',
          background: value ? '#16a34a' : '#cbd5e1',
          position: 'relative',
        }}
      >
        <div
          style={{
            position: 'absolute',
            top: 2,
            left: value ? 18 : 2,
            width: 16,
            height: 16,
            borderRadius: '50%',
            background: 'white',
            transition: 'left 0.2s',
            boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
          }}
        />
      </button>
    </div>
  );
}

export default TweaksPanel;