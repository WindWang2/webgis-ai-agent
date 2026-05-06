'use client';

import { useHudStore } from '@/lib/store/useHudStore';

interface MapToolbarProps {
  hudOpen?: boolean;
  onToggleHud?: () => void;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onHome?: () => void;
  onLocate?: () => void;
  onExport?: () => void;
}

export default function MapToolbar({
  hudOpen = false,
  onToggleHud,
  onZoomIn,
  onZoomOut,
  onHome,
  onLocate,
  onExport,
}: MapToolbarProps) {
  const is3D = useHudStore((s) => s.is3D);
  const setIs3D = useHudStore((s) => s.setIs3D);

  return (
    <div
      style={{
        position: 'absolute',
        top: '50%',
        right: hudOpen ? 340 : 10,
        transform: 'translateY(-50%)',
        zIndex: 40,
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        background: 'rgba(255,255,255,0.85)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.9)',
        boxShadow: '0 4px 24px rgba(15,23,42,0.10)',
        borderRadius: 12,
        padding: 3,
        transition: 'right 0.22s cubic-bezier(0.4,0,0.2,1)',
      }}
    >
      {/* Zoom in */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onZoomIn} title='放大'>
        <svg width='14' height='14' viewBox='0 0 14 14' fill='none' style={{ display: 'block' }}>
          <circle cx='6' cy='6' r='4.5' stroke='currentColor' strokeWidth='1.3'/>
          <path d='M10 10l3 3' stroke='currentColor' strokeWidth='1.4' strokeLinecap='round'/>
          <path d='M4 6h4M6 4v4' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round'/>
        </svg>
      </button>

      {/* Zoom out */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onZoomOut} title='缩小'>
        <svg width='14' height='14' viewBox='0 0 14 14' fill='none' style={{ display: 'block' }}>
          <circle cx='6' cy='6' r='4.5' stroke='currentColor' strokeWidth='1.3'/>
          <path d='M10 10l3 3' stroke='currentColor' strokeWidth='1.4' strokeLinecap='round'/>
          <path d='M4 6h4' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round'/>
        </svg>
      </button>

      {/* Home */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onHome} title='复位'>
        <svg width='14' height='14' viewBox='0 0 14 14' fill='none' style={{ display: 'block' }}>
          <path d='M2 6.5l5-4.5 5 4.5V12H9V9H5v3H2V6.5z' stroke='currentColor' strokeWidth='1.3' strokeLinejoin='round'/>
        </svg>
      </button>

      {/* Locate */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onLocate} title='定位我'>
        <svg width='14' height='14' viewBox='0 0 14 14' fill='none' style={{ display: 'block' }}>
          <circle cx='7' cy='7' r='2.5' stroke='currentColor' strokeWidth='1.2'/>
          <path d='M7 1v2.5M7 10.5V13M1 7h2.5M10.5 7H13' stroke='currentColor' strokeWidth='1.2' strokeLinecap='round'/>
        </svg>
      </button>

      {/* Divider */}
      <div style={{ width: 20, height: 1, background: 'rgba(15,23,42,0.08)', margin: '2px auto' }} />

      {/* 2D/3D toggle */}
      <button
        onClick={() => setIs3D(!is3D)}
        title={is3D ? '切换 2D' : '切换 3D'}
        style={{
          ...buttonStyle,
          fontSize: '9.5px',
          fontWeight: 700,
          letterSpacing: '0.06em',
          background: is3D ? 'rgba(22,163,74,0.1)' : 'transparent',
          color: is3D ? '#15803d' : '#64748b',
        }}
      >
        {is3D ? '3D' : '2D'}
      </button>

      {/* HUD toggle */}
      <button
        onClick={onToggleHud}
        title='Agent 环境感知'
        style={{
          ...buttonStyle,
          background: hudOpen ? 'rgba(139,92,246,0.12)' : 'transparent',
          color: hudOpen ? '#7c3aed' : '#64748b',
        }}
      >
        <svg width='14' height='14' viewBox='0 0 14 14' fill='none' style={{ display: 'block' }}>
          <circle cx='7' cy='7' r='5.5' stroke='currentColor' strokeWidth='1.2'/>
          <circle cx='7' cy='7' r='2' stroke='currentColor' strokeWidth='1.2'/>
          <path d='M7 1.5v2M7 10.5v2M1.5 7h2M10.5 7h2' stroke='currentColor' strokeWidth='1.1' strokeLinecap='round'/>
        </svg>
      </button>

      {/* Export */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onExport} title='导出地图'>
        <svg width='14' height='14' viewBox='0 0 14 14' fill='none' style={{ display: 'block' }}>
          <path d='M7 2v7M4.5 6.5L7 9l2.5-2.5' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round' strokeLinejoin='round'/>
          <path d='M2 11h10' stroke='currentColor' strokeWidth='1.3' strokeLinecap='round'/>
        </svg>
      </button>
    </div>
  );
}

const buttonStyle = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 32,
  height: 32,
  borderRadius: 8,
  border: 'none',
  background: 'transparent',
  cursor: 'pointer',
  transition: 'all 0.1s',
  fontFamily: "'JetBrains Mono', monospace",
} as const;