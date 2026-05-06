'use client';

import { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';

interface BaselayerSwitcherProps {
  className?: string;
}

const BASELAYERS = [
  { id: 'osm', label: 'OpenStreetMap' },
  { id: 'amap', label: '高德地图' },
  { id: 'satellite', label: '卫星影像' },
  { id: 'dark', label: '暗色底图' },
  { id: 'tianditu', label: '天地图' },
];

export function BaselayerSwitcher({ className }: BaselayerSwitcherProps) {
  const [open, setOpen] = useState(false);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const setBaseLayer = useHudStore((s) => s.setBaseLayer);

  const currentLabel = BASELAYERS.find((l) => l.id === baseLayer)?.label || baseLayer;

  return (
    <div style={{ position: 'relative' }} className={className}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          padding: '5px 10px',
          borderRadius: 8,
          background: 'rgba(255,255,255,0.88)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: '1px solid rgba(255,255,255,0.92)',
          boxShadow: '0 2px 12px rgba(15,23,42,0.08)',
          fontSize: '10.5px',
          color: '#475569',
          cursor: 'pointer',
          fontFamily: "'JetBrains Mono', monospace",
          display: 'flex',
          alignItems: 'center',
          gap: 5,
        }}
      >
        <svg width='11' height='11' viewBox='0 0 11 11' fill='none' style={{ display: 'block' }}>
          <path d='M5.5 1L1 4l4.5 2.5L10 4 5.5 1z' stroke='#94a3b8' strokeWidth='1'/>
          <path d='M1 7l4.5 2.5L10 7' stroke='#94a3b8' strokeWidth='1' strokeLinecap='round'/>
        </svg>
        {currentLabel}
        <svg width='8' height='8' viewBox='0 0 8 8' fill='none' style={{ display: 'block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
          <path d='M1 2.5l3 3 3-3' stroke='#94a3b8' strokeWidth='1.2' strokeLinecap='round'/>
        </svg>
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            right: 0,
            marginBottom: 4,
            background: 'rgba(252,253,254,0.96)',
            backdropFilter: 'blur(20px)',
            WebkitBackdropFilter: 'blur(20px)',
            border: '1px solid rgba(255,255,255,0.92)',
            boxShadow: '0 4px 24px rgba(15,23,42,0.09)',
            borderRadius: 10,
            overflow: 'hidden',
            minWidth: 140,
          }}
        >
          {BASELAYERS.map((layer) => (
            <button
              key={layer.id}
              onClick={() => {
                setBaseLayer(layer.id);
                setOpen(false);
              }}
              style={{
                display: 'block',
                width: '100%',
                padding: '7px 12px',
                border: 'none',
                background: layer.id === baseLayer ? 'rgba(22,163,74,0.07)' : 'transparent',
                color: layer.id === baseLayer ? '#15803d' : '#475569',
                fontSize: 11,
                cursor: 'pointer',
                textAlign: 'left',
                fontFamily: "'DM Sans', system-ui, sans-serif",
                fontWeight: layer.id === baseLayer ? 500 : 400,
              }}
              onMouseEnter={(e) => {
                if (layer.id !== baseLayer) {
                  e.currentTarget.style.background = 'rgba(15,23,42,0.04)';
                }
              }}
              onMouseLeave={(e) => {
                if (layer.id !== baseLayer) {
                  e.currentTarget.style.background = 'transparent';
                }
              }}
            >
              {layer.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default BaselayerSwitcher;