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
  const rootRef = useRef<HTMLDivElement>(null);

  const currentLabel = TILE_PROVIDERS[selectedBaseLayer]?.name || baseLayer || 'Carto 浅色';

  // Sync index from session-loaded baseLayer name (async SDM sets the name; index defaults to 1).
  // #550: baseLayer can carry a STALE legacy name (pre-fix demo vocabulary like
  // 'OSM Voyager' persisted in localStorage). findIndex returns -1 and the old
  // code silently swallowed it — name/index desync healed nowhere. Now a mismatch
  // heals to a canonical provider so label and rendered tiles agree again.
  useEffect(() => {
    if (!baseLayer) return;
    const idx = TILE_PROVIDERS.findIndex((p) => p.name === baseLayer);
    if (idx === -1) {
      const fallbackIdx = Math.max(
        0,
        TILE_PROVIDERS.findIndex((p) => p.name === 'Carto 深色')
      );
      const fallback = TILE_PROVIDERS[fallbackIdx];
      if (fallback) {
        setSelectedBaseLayer(fallbackIdx);
        setBaseLayer(fallback.name);
      }
      return;
    }
    if (idx !== selectedBaseLayer) {
      setSelectedBaseLayer(idx);
    }
  }, [baseLayer]); // eslint-disable-line react-hooks/exhaustive-deps

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
      {/* C: 下拉浮在地图上 —— 之前用 backdrop-filter: blur(12px)，对持续重绘的
          画布是最贵的那类合成；改走 .map-chrome 不透明容器配方（主题色由
          --map-chrome-* 提供，不再按 isDark 手写两套 hex）。 */}
      <button
        type='button'
        aria-haspopup='listbox'
        aria-expanded={open}
        aria-label={`Base layer: ${currentLabel}`}
        onClick={() => setOpen(!open)}
        className='map-chrome flex cursor-pointer items-center gap-1.5 px-2.5 py-1 text-body font-mono text-map-chrome-ink'
      >
        <svg width='11' height='11' viewBox='0 0 11 11' fill='none' stroke='currentColor' strokeWidth='1' className='text-map-chrome-ink-muted' style={{ display: 'block' }}>
          <path d='M5.5 1L1 4l4.5 2.5L10 4 5.5 1z' />
          <path d='M1 7l4.5 2.5L10 7' strokeLinecap='round'/>
        </svg>
        {currentLabel}
        <svg width='8' height='8' viewBox='0 0 8 8' fill='none' stroke='currentColor' strokeWidth='1.2' strokeLinecap='round' className='text-map-chrome-ink-muted' style={{ display: 'block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
          <path d='M1 2.5l3 3 3-3' />
        </svg>
      </button>

      {open && (
        <div
          role='listbox'
          aria-label='Base layer options'
          className='map-chrome absolute right-0 top-full z-30 mt-1 max-h-[340px] min-w-[160px] overflow-y-auto py-1'
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
                  background: isActive ? 'var(--accent-soft)' : 'transparent',
                  /* 选中项文字是 accent 作文字 —— 用 text-safe 的 --agent-accent-text。 */
                  color: isActive ? 'var(--agent-accent)' : 'var(--map-chrome-text)',
                  fontSize: 13,
                  cursor: 'pointer',
                  textAlign: 'left',
                  fontFamily: "'DM Sans', system-ui, sans-serif",
                  fontWeight: isActive ? 500 : 400,
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'var(--surface-hover)';
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
