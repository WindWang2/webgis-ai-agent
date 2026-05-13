'use client';

import { useEffect, useState } from 'react';
import {
  PanelLeftClose,
  Menu,
  Compass,
  Plus,
  History,
  Settings,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import BaselayerSwitcher from '@/components/map/baselayer-switcher';

interface TopBarProps {
  sessionName?: string;
  onNewSession?: () => void;
}

export default function TopBar({ sessionName = '未命名', onNewSession }: TopBarProps) {
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const toggleLeftPanel = useHudStore((s) => s.toggleLeftPanel);
  const aiStatus = useHudStore((s) => s.aiStatus);
  const setSettingsOpen = useHudStore((s) => s.setSettingsOpen);
  const setHistoryOpen = useHudStore((s) => s.setHistoryOpen);
  const theme = useHudStore((s) => s.theme);
  const accentColor = useHudStore((s) => s.accentColor);
  const isDark = theme === 'dark';

  const isActive = aiStatus === 'thinking' || aiStatus === 'acting';

  const getStatusConfig = (status: string) => {
    switch (status) {
      case 'idle': return { label: '就绪', color: isDark ? '#64748b' : '#94a3b8', bg: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)' };
      case 'thinking': case 'acting': return { label: status === 'thinking' ? '感知中' : '执行中', color: accentColor, bg: isDark ? `${accentColor}15` : `${accentColor}10` };
      case 'done': return { label: '完成', color: isDark ? '#4ade80' : '#16a34a', bg: isDark ? 'rgba(74,222,128,0.15)' : 'rgba(16,185,129,0.10)' };
      case 'error': return { label: '异常', color: isDark ? '#fca5a5' : '#ef4444', bg: isDark ? 'rgba(248,113,113,0.15)' : 'rgba(254,226,226,0.6)' };
      default: return { label: '就绪', color: isDark ? '#64748b' : '#94a3b8', bg: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)' };
    }
  };

  const status = getStatusConfig(aiStatus);

  /* scan-line position 0-100% */
  const [scanX, setScanX] = useState(0);
  useEffect(() => {
    if (!isActive) return;
    let frame: number;
    let start: number | null = null;
    const DURATION = 2000;
    const tick = (ts: number) => {
      if (start === null) start = ts;
      const progress = ((ts - start) % DURATION) / DURATION;
      setScanX(progress * 100);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isActive]);

  return (
    <div
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
        display: 'flex', alignItems: 'center', height: 42, paddingLeft: 8, paddingRight: 8, gap: 8,
        backgroundColor: isDark ? 'rgba(15,23,42,0.75)' : 'rgba(255,255,255,0.75)',
        backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
        borderBottomWidth: isActive ? 2 : 1,
        borderBottomStyle: 'solid',
        borderBottomColor: isActive ? `${accentColor}55` : isDark ? 'rgba(148,163,184,0.2)' : 'rgba(0,0,0,0.06)'
      }}
    >
      {/* heartbeat scan line */}
      {isActive && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, overflow: 'hidden', pointerEvents: 'none' }}>
          <div
            style={{
              background: `linear-gradient(90deg, transparent 0%, ${accentColor}99 50%, transparent 100%)`,
              width: '40%',
              transform: `translateX(${scanX * 2.5}%)`,
              height: '100%'
            }}
          />
        </div>
      )}

      {/* sidebar toggle */}
      <button
        onClick={toggleLeftPanel}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 28, height: 28, borderRadius: 6, cursor: 'pointer',
          color: isDark ? '#e2e8f0' : '#475569', backgroundColor: 'transparent'
        }}
        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
        title={leftPanelOpen ? '收起侧栏' : '展开侧栏'}
      >
        {leftPanelOpen ? <PanelLeftClose size={15} /> : <Menu size={15} />}
      </button>

      {/* logo */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, userSelect: 'none' }}>
        <span
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 24, height: 24, borderRadius: 6,
            background: `linear-gradient(135deg, ${accentColor}, ${accentColor}dd)`
          }}
        >
          <Compass size={13} style={{ color: '#fff' }} />
        </span>
        <div style={{ lineHeight: 1 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: isDark ? '#e2e8f0' : '#1e293b' }}>
            GeoAgent
          </span>
          <span style={{ fontSize: 9, marginLeft: 4, color: isDark ? '#64748b' : '#94a3b8' }}>All is Agent</span>
        </div>
      </div>

      {/* session name pill */}
      <span
        style={{
          marginLeft: 4, padding: '2px 8px', borderRadius: 999,
          backgroundColor: isDark ? 'rgba(30,41,59,0.6)' : 'rgba(226,232,240,0.6)',
          fontSize: 10, color: isDark ? '#94a3b8' : '#64748b',
          borderWidth: 1, borderStyle: 'solid',
          borderColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.8)',
          maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
        }}
      >
        会话 / {sessionName}
      </span>

      {/* spacer */}
      <div style={{ flex: 1 }} />

      {/* agent status badge */}
      <span
        style={{
          display: 'flex', alignItems: 'center', gap: 4, padding: '2px 8px',
          borderRadius: 999, backgroundColor: status.bg, fontSize: 10, fontWeight: 500
        }}
      >
        <span
          style={{
            width: 6, height: 6, borderRadius: '50%', backgroundColor: status.color
          }}
        />
        <span style={{ color: isDark ? '#e2e8f0' : '#1e293b' }}>{status.label}</span>
      </span>

      {/* right actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <button
          onClick={onNewSession}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 28, height: 28, borderRadius: 6, cursor: 'pointer',
            color: isDark ? '#94a3b8' : '#64748b', backgroundColor: 'transparent'
          }}
          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; e.currentTarget.style.color = isDark ? '#e2e8f0' : '#475569'; }}
          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#94a3b8' : '#64748b'; }}
          title="新建会话"
        >
          <Plus size={15} />
        </button>

        <button
          onClick={() => setHistoryOpen(true)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 28, height: 28, borderRadius: 6, cursor: 'pointer',
            color: isDark ? '#94a3b8' : '#64748b', backgroundColor: 'transparent'
          }}
          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; e.currentTarget.style.color = isDark ? '#e2e8f0' : '#475569'; }}
          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#94a3b8' : '#64748b'; }}
          title="历史记录"
        >
          <History size={15} />
        </button>

        <span style={{ marginLeft: 4, marginRight: 4, width: 1, height: 16, backgroundColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.8)' }} />

        <BaselayerSwitcher />

        <span style={{ marginLeft: 4, marginRight: 4, width: 1, height: 16, backgroundColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.8)' }} />

        <button
          onClick={() => setSettingsOpen(true)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 28, height: 28, borderRadius: 6, cursor: 'pointer',
            color: isDark ? '#94a3b8' : '#64748b', backgroundColor: 'transparent'
          }}
          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; e.currentTarget.style.color = isDark ? '#e2e8f0' : '#475569'; }}
          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#94a3b8' : '#64748b'; }}
          title="设置"
        >
          <Settings size={15} />
        </button>
      </div>
    </div>
  );
}
