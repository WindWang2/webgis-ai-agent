'use client';

import { useState, useEffect, useRef } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';

export function SpatialCrosshair() {
  const aiStatus = useHudStore((s) => s.aiStatus);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isThinking = aiStatus === 'thinking' || aiStatus === 'acting';

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) {
        clearTimeout(copyTimerRef.current);
        copyTimerRef.current = null;
      }
    };
  }, []);

  const handleCopy = () => {
    const vp = useHudStore.getState().viewport;
    const [cLng, cLat] = vp?.center ?? [0, 0];
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(`${cLng.toFixed(6)}, ${cLat.toFixed(6)}`);
    }
    setCopied(true);
    if (copyTimerRef.current) {
      clearTimeout(copyTimerRef.current);
    }
    copyTimerRef.current = setTimeout(() => {
      setCopied(false);
      copyTimerRef.current = null;
    }, 1500);
  };

  return (
    <div style={{
      position: 'absolute',
      inset: 0,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      pointerEvents: 'none',
      zIndex: 20
    }}>
      <style>{`
        @keyframes spin-clockwise {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes spin-counterclockwise {
          from { transform: rotate(360deg); }
          to { transform: rotate(0deg); }
        }
        @keyframes radar-pulse {
          0% { transform: scale(0.9); opacity: 0.8; }
          50% { transform: scale(1.15); opacity: 0.3; }
          100% { transform: scale(0.9); opacity: 0.8; }
        }
        @keyframes laser-sweep {
          0% { transform: translateY(-40px); opacity: 0; }
          10% { opacity: 0.8; }
          90% { opacity: 0.8; }
          100% { transform: translateY(40px); opacity: 0; }
        }
      `}</style>

      {/* Central Crosshair & Reticle Container */}
      <div style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 160,
        height: 160,
      }}>
        {/* Outer Rotating Compass Ring */}
        <div style={{
          position: 'absolute',
          width: 140,
          height: 140,
          border: '1px dashed rgba(22, 163, 74, 0.25)',
          borderRadius: '50%',
          borderColor: isThinking ? 'color-mix(in srgb, var(--agent-accent) 47%, transparent)' : 'rgba(148, 163, 184, 0.2)',
          animation: isThinking ? 'spin-clockwise 15s linear infinite' : 'spin-clockwise 45s linear infinite',
          transition: 'border-color 0.3s ease',
        }}>
          {/* Compass Ticks */}
          {[0, 90, 180, 270].map((deg) => (
            <div key={deg} style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              width: 1,
              height: 6,
              background: isThinking ? 'var(--agent-accent)' : '#64748b',
              transform: `translate(-50%, -50%) rotate(${deg}deg) translateY(-67px)`,
              transition: 'background-color 0.3s ease',
            }} />
          ))}
        </div>

        {/* Inner Counter-Rotating Hexagon Ring */}
        <div style={{
          position: 'absolute',
          width: 80,
          height: 80,
          border: '1px solid rgba(22, 163, 74, 0.15)',
          borderRadius: '30%',
          borderColor: isThinking ? 'color-mix(in srgb, var(--agent-accent) 33%, transparent)' : 'rgba(148, 163, 184, 0.15)',
          animation: 'spin-counterclockwise 12s linear infinite',
          transition: 'border-color 0.3s ease',
        }} />

        {/* Sensory Pulse Ring */}
        {isThinking && (
          <div style={{
            position: 'absolute',
            width: 110,
            height: 110,
            border: '2px solid var(--agent-accent)',
            borderRadius: '50%',
            opacity: 0.4,
            animation: 'radar-pulse 2s ease-in-out infinite',
            pointerEvents: 'none',
          }} />
        )}

        {/* Center Precise Crosshair — 点击/Enter 复制中心坐标 */}
        <svg
          onClick={handleCopy}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              handleCopy();
            }
          }}
          role="button"
          tabIndex={0}
          aria-label={copied ? '坐标已复制' : '复制当前地图中心坐标'}
          width="24"
          height="24" 
          viewBox="0 0 24 24" 
          fill="none" 
          style={{
            position: 'absolute',
            zIndex: 10,
            filter: copied
              ? 'drop-shadow(0 0 6px var(--agent-accent))'
              : (isThinking ? 'drop-shadow(0 0 4px var(--agent-accent))' : 'none'),
            transition: 'filter 0.3s ease',
            cursor: 'pointer',
            // 审计：这里恒为 'auto'，于是地图正中央始终有一个 24px 的点击陷阱，
            // 空闲时也会吞掉要素拾取。只有真正可复制时才接收指针事件。
            pointerEvents: copied || isThinking ? 'auto' : 'none',
          }}
        >
          <title>{copied ? "已复制！" : "点击复制当前中心坐标"}</title>
          <circle cx="12" cy="12" r="3" fill={copied || isThinking ? 'var(--agent-accent)' : 'var(--text-disabled)'} style={{ transition: 'fill 0.3s ease' }} />
          <path d="M12 2v6M12 16v6M2 12h6M16 12h6" stroke={copied || isThinking ? 'var(--agent-accent)' : 'var(--text-muted)'} strokeWidth="1.5" strokeLinecap="round" style={{ transition: 'stroke 0.3s ease' }} />
        </svg>

        {/* Laser Scanning Line Overlay */}
        {isThinking && (
          <div style={{
            position: 'absolute',
            width: 120,
            height: 2,
            background: 'linear-gradient(90deg, transparent, var(--agent-accent), transparent)',
            boxShadow: '0 0 8px var(--agent-accent)',
            animation: 'laser-sweep 1.8s ease-in-out infinite',
            zIndex: 5,
          }} />
        )}
      </div>
    </div>
  );
}

export default SpatialCrosshair;

