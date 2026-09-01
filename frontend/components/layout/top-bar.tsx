'use client';

import { useEffect, useState } from 'react';
import {
  PanelLeftClose,
  Menu,
  Compass,
  Plus,
  History,
  Settings,
  Sliders,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
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
  const setTweaksOpen = useHudStore((s) => s.setTweaksOpen);
  const is3D = useHudStore((s) => s.is3D);
  const setIs3D = useHudStore((s) => s.setIs3D);

  const isActive = aiStatus === 'thinking' || aiStatus === 'acting';

  const getStatusConfig = (status: string) => {
    switch (status) {
      case 'idle': return { label: '就绪', color: 'var(--text-muted)', bg: 'var(--surface-sunken)' };
      case 'thinking': case 'acting': return { label: status === 'thinking' ? '感知中' : '执行中', color: 'var(--agent-accent)', bg: 'color-mix(in srgb, var(--agent-accent) 8%, transparent)' };
      // V4：走与 StatusBadge / InlineNotice / toast 同一套语义 token，
      // 不再各写一对明暗 hex（原先是 #4ade80/#16a34a 与 #fca5a5/#ef4444）。
      case 'done': return { label: '完成', color: 'var(--success)', bg: 'var(--success-soft)' };
      case 'error': return { label: '异常', color: 'var(--critical)', bg: 'var(--critical-soft)' };
      default: return { label: '就绪', color: 'var(--text-muted)', bg: 'var(--surface-sunken)' };
    }
  };

  const status = getStatusConfig(aiStatus);

  /* scan-line position 0-100%（prefers-reduced-motion 下不启动 rAF） */
  const [scanX, setScanX] = useState(0);
  const reducedMotion = usePrefersReducedMotion();
  useEffect(() => {
    if (!isActive || reducedMotion) return;
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
  }, [isActive, reducedMotion]);

  return (
    // V4：壳层度量改用 token（h-topbar），背景改为不透明 surface-panel 并
    // 移除 backdrop-blur —— 顶栏压在持续重绘的地图画布上，blur 是最贵的
    // 那一类滤镜，半透明又会让地图细节透进密集文本。
    <div
      className="fixed inset-x-0 top-0 z-50 flex h-topbar items-center gap-2.5 bg-surface-panel px-3"
      style={{
        borderBottomWidth: isActive ? 2 : 1,
        borderBottomStyle: 'solid',
        borderBottomColor: isActive ? 'color-mix(in srgb, var(--agent-accent) 33%, transparent)' : 'var(--border-subtle)'
      }}
    >
      {/* heartbeat scan line */}
      {isActive && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, overflow: 'hidden', pointerEvents: 'none' }}>
          <div
            style={{
              background: 'linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--agent-accent) 60%, transparent) 50%, transparent 100%)',
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
        aria-label={leftPanelOpen ? '收起侧栏' : '展开侧栏'}
        title={leftPanelOpen ? '收起侧栏' : '展开侧栏'}
        className="flex h-control-md w-control-md items-center justify-center rounded-sm text-ink transition-colors hover:bg-surface-hover"
      >
        {leftPanelOpen ? <PanelLeftClose size={14} aria-hidden /> : <Menu size={14} aria-hidden />}
      </button>

      {/* logo */}
      <div className="flex select-none items-center gap-1.5 shrink-0">
        <span
          aria-hidden
          className="flex h-6 w-6 items-center justify-center rounded-sm"
          style={{
            background: 'linear-gradient(135deg, var(--agent-accent), color-mix(in srgb, var(--agent-accent) 87%, transparent))'
          }}
        >
          <Compass size={13} className="text-ink-on-accent" />
        </span>
        <div className="leading-tight">
          <span className="text-heading font-semibold text-ink">
            GeoAgent
          </span>
          <span className="ml-1 hidden min-[480px]:inline text-caption text-ink-muted">All is Agent</span>
        </div>
      </div>

      {/* session name pill */}
      <span className="ml-1 hidden sm:inline-block max-w-[140px] md:max-w-[180px] truncate rounded-pill border border-edge-subtle bg-surface-sunken px-2 py-0.5 text-meta text-ink-secondary">
        会话 / {sessionName}
      </span>

      {/* spacer */}
      <div className="flex-1 min-w-[4px]" />

      {/* agent status badge */}
      <span
        className="flex shrink-0 items-center gap-1 rounded-pill px-2 py-0.5 text-meta font-medium"
        style={{ backgroundColor: status.bg }}
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-pill"
          style={{ backgroundColor: status.color }}
        />
        <span className="text-ink">{status.label}</span>
      </span>

      {/* right actions */}
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          onClick={onNewSession}
          aria-label="新建会话"
          title="新建会话"
          className="flex h-control-md w-control-md items-center justify-center rounded-sm text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <Plus size={14} aria-hidden />
        </button>

        <button
          onClick={() => setHistoryOpen(true)}
          aria-label="历史记录"
          title="历史记录"
          className="flex h-control-md w-control-md items-center justify-center rounded-sm text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <History size={14} aria-hidden />
        </button>

        <span aria-hidden className="mx-1 h-4 w-px bg-edge-subtle" />

        <BaselayerSwitcher />

        <button
          type='button'
          onClick={() => setIs3D(!is3D)}
          aria-label={is3D ? '切换至 2D 视图' : '切换至 3D 视角'}
          title={is3D ? '视角: 3D (点击切换 2D)' : '视角: 2D (点击切换 3D)'}
          className="flex items-center gap-1 rounded-md border border-edge-subtle bg-surface-overlay px-2.5 py-1 font-mono text-body text-ink-secondary shadow-overlay transition-colors hover:bg-surface-hover"
        >
          <svg width='11' height='11' viewBox='0 0 11 11' fill='none' style={{ display: 'block' }}>
            {is3D ? (
              <path d='M5.5 1.5L2 3.5l3.5 2L9 3.5 5.5 1.5z M2 6l3.5 2L9 6 M2 8.5l3.5 2 3.5-2' stroke='var(--success)' strokeWidth='1' strokeLinecap='round' strokeLinejoin='round'/>
            ) : (
              <path d='M5.5 2.5L2 4.5l3.5 2 3.5-2-3.5-2z' stroke='var(--text-secondary)' strokeWidth='1' strokeLinecap='round' strokeLinejoin='round'/>
            )}
          </svg>
          <span
            className={is3D ? 'font-semibold' : undefined}
            style={{ color: is3D ? 'var(--success)' : undefined }}
          >
            {is3D ? '3D' : '2D'}
          </span>
        </button>

        <span aria-hidden className="mx-1 h-4 w-px bg-edge-subtle" />

        {/* #551：Tweaks 面板此前没有任何生产入口调用 setTweaksOpen(true) ——
            面板永远不可达。这里补上真实 opener（顶栏 UI 调整按钮）。 */}
        <button
          onClick={() => setTweaksOpen(true)}
          aria-label="UI 调整"
          title="UI 调整"
          className="flex h-control-md w-control-md items-center justify-center rounded-sm text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <Sliders size={14} aria-hidden />
        </button>

        <button
          onClick={() => setSettingsOpen(true)}
          aria-label="设置"
          title="设置"
          className="flex h-control-md w-control-md items-center justify-center rounded-sm text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <Settings size={14} aria-hidden />
        </button>
      </div>
    </div>
  );
}
