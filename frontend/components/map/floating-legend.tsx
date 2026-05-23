'use client';

import { useHudStore } from '@/lib/store/useHudStore';

interface FloatingLegendProps {
  className?: string;
}

const COLORS = ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'];
const LABELS = ['极低', '低', '中', '高', '极高'];

export function FloatingLegend({ className }: FloatingLegendProps) {
  const layers = useHudStore((s) => s.layers);
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';
  const visibleHeatLayer = layers.find((l) => l.visible && l.type === 'heatmap');

  return (
    <div
      style={{
        background: isDark ? 'rgba(15, 23, 42, 0.85)' : 'rgba(252,253,254,0.92)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: isDark ? '1px solid rgba(255, 255, 255, 0.06)' : '1px solid rgba(255,255,255,0.92)',
        boxShadow: isDark ? '0 4px 24px rgba(0,0,0,0.4)' : '0 4px 24px rgba(15,23,42,0.09)',
        borderRadius: 10,
        padding: '8px 12px',
        fontSize: '10.5px',
        fontFamily: "'DM Sans', system-ui, sans-serif",
        minWidth: 140,
        transform: visibleHeatLayer ? 'translateY(0)' : 'translateY(20px)',
        opacity: visibleHeatLayer ? 1 : 0,
        transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
        pointerEvents: visibleHeatLayer ? 'auto' : 'none',
      }}
      className={className}
    >
      <div style={{ fontSize: 10, color: isDark ? '#64748b' : '#94a3b8', fontFamily: "'JetBrains Mono', monospace", textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
        {visibleHeatLayer?.name || ''}
      </div>
      <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 5 }}>
        {COLORS.map((color, idx) => (
          <div key={idx} style={{ flex: 1, backgroundColor: color }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: isDark ? '#94a3b8' : '#64748b', fontSize: 9 }}>
        {LABELS.map((label, idx) => (
          <span key={idx}>{label}</span>
        ))}
      </div>
    </div>
  );
}

export default FloatingLegend;