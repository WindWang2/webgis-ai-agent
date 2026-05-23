'use client';

import { useState, useRef, useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { TILE_PROVIDERS } from '@/lib/providers';

interface BaselayerSwitcherProps {
  className?: string;
}

/**
 * Baselayer dropdown — single source of truth is TILE_PROVIDERS (lib/providers.ts).
 * Each provider's canonical Chinese name (e.g. "Carto 深色") is what the AI's
 * env summary uses too. Clicking an item dual-writes to BOTH state stores:
 *   - useMapAction.setSelectedBaseLayer(index)  — drives actual MAP_STYLES[index]
 *   - useHudStore.setBaseLayer(canonicalName)    — drives status bar / HUD panel label
 * If either is skipped, dropdown click silently no-ops or labels drift out of sync.
 */
export function BaselayerSwitcher({ className }: BaselayerSwitcherProps) {
  const [open, setOpen] = useState(false);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const setBaseLayer = useHudStore((s) => s.setBaseLayer);
  const { selectedBaseLayer, setSelectedBaseLayer } = useMapAction();
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';
  const rootRef = useRef<HTMLDivElement>(null);

  const currentLabel = TILE_PROVIDERS[selectedBaseLayer]?.name || baseLayer || 'Carto 浅色';

  // Close on Escape + click-outside (a11y from /review)
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onMouseDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} style={{ position: 'relative' }} className={className}>
      <button
        type='button'
        aria-haspopup='listbox'
        aria-expanded={open}
        aria-label={`Base layer: ${currentLabel}`}
        onClick={() => setOpen(!open)}
        style={{
          padding: '5px 10px',
          borderRadius: 8,
          background: isDark ? 'rgba(15, 23, 42, 0.72)' : 'rgba(255,255,255,0.88)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: isDark ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(255,255,255,0.92)',
          boxShadow: isDark ? '0 2px 12px rgba(0,0,0,0.3)' : '0 2px 12px rgba(15,23,42,0.08)',
          fontSize: '10.5px',
          color: isDark ? '#cbd5e1' : '#475569',
          cursor: 'pointer',
          fontFamily: "'JetBrains Mono', monospace",
          display: 'flex',
          alignItems: 'center',
          gap: 5,
        }}
      >
        <svg width='11' height='11' viewBox='0 0 11 11' fill='none' style={{ display: 'block' }}>
          <path d='M5.5 1L1 4l4.5 2.5L10 4 5.5 1z' stroke={isDark ? '#475569' : '#94a3b8'} strokeWidth='1'/>
          <path d='M1 7l4.5 2.5L10 7' stroke={isDark ? '#475569' : '#94a3b8'} strokeWidth='1' strokeLinecap='round'/>
        </svg>
        {currentLabel}
        <svg width='8' height='8' viewBox='0 0 8 8' fill='none' style={{ display: 'block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
          <path d='M1 2.5l3 3 3-3' stroke={isDark ? '#475569' : '#94a3b8'} strokeWidth='1.2' strokeLinecap='round'/>
        </svg>
      </button>

      {open && (
        <div
          role='listbox'
          aria-label='Base layer options'
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: 4,
            background: isDark ? 'rgba(15, 23, 42, 0.95)' : 'rgba(252,253,254,0.96)',
            backdropFilter: 'blur(20px)',
            WebkitBackdropFilter: 'blur(20px)',
            border: isDark ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(255,255,255,0.92)',
            boxShadow: isDark ? '0 4px 24px rgba(0,0,0,0.5)' : '0 4px 24px rgba(15,23,42,0.09)',
            borderRadius: 10,
            overflow: 'hidden',
            minWidth: 160,
            maxHeight: 340,
            overflowY: 'auto',
          }}
        >
          {TILE_PROVIDERS.map((provider, idx) => {
            const isActive = idx === selectedBaseLayer;
            return (
              <button
                key={provider.name}
                type='button'
                role='option'
                aria-selected={isActive}
                onClick={() => {
                  // Dual-write: both stores must agree or we end up with the bug
                  // QA-2026-05-20 ISSUE-001/002/003 fixed
                  setSelectedBaseLayer(idx);
                  setBaseLayer(provider.name);
                  setOpen(false);
                }}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: '7px 12px',
                  border: 'none',
                  background: isActive ? (isDark ? 'rgba(22,163,74,0.15)' : 'rgba(22,163,74,0.07)') : 'transparent',
                  color: isActive ? (isDark ? '#4ade80' : '#15803d') : (isDark ? '#94a3b8' : '#475569'),
                  fontSize: 11,
                  cursor: 'pointer',
                  textAlign: 'left',
                  fontFamily: "'DM Sans', system-ui, sans-serif",
                  fontWeight: isActive ? 500 : 400,
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(15,23,42,0.04)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'transparent';
                  }
                }}
              >
                {provider.name}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default BaselayerSwitcher;
