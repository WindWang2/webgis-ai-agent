'use client';

import { useEffect, useRef, useState } from 'react';
import {
  LocateFixed,
  LoaderCircle,
  PanelLeftClose,
  Menu,
  Compass,
  Plus,
  History,
  Settings,
  Sliders,
  MoreHorizontal,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
import { useT } from '@/lib/i18n/useT';
import type { GeolocationError } from '@/lib/hooks/use-geolocation';
// #1667：窄档（≤768px）右侧集群折叠为「更多」收纳菜单。布局模式走 ADR-0144
// 三档唯一真相（matchMedia + useSyncExternalStore）而非裸 CSS 断点 —— 同一
// 判据在 vitest 里可装 matchMedia mock 直接断言折叠行为（390px 命中测试）。
import { useLayoutMode } from '@/lib/hooks/use-layout-mode';
import { useHydrated } from '@/lib/hooks/use-hydrated';
import BaselayerSwitcher from '@/components/map/baselayer-switcher';

interface TopBarProps {
  sessionName?: string;
  onNewSession?: () => void;
  onRequestLocation?: () => void;
  locationLoading?: boolean;
  locationError?: GeolocationError | null;
}

const iconButton = 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink sm:h-9 sm:w-9';
const menuItem = 'flex min-h-9 w-full items-center gap-2 px-3 text-left text-body text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink';

/** 2D/3D 视角 glyph（内联集群与「更多」收纳菜单共用同一图形）。 */
function ViewToggleGlyph({ is3D }: { is3D: boolean }) {
  return (
    <svg width='11' height='11' viewBox='0 0 11 11' fill='none' style={{ display: 'block' }}>
      {is3D ? (
        <path d='M5.5 1.5L2 3.5l3.5 2L9 3.5 5.5 1.5z M2 6l3.5 2L9 6 M2 8.5l3.5 2 3.5-2' stroke='var(--success)' strokeWidth='1' strokeLinecap='round' strokeLinejoin='round'/>
      ) : (
        <path d='M5.5 2.5L2 4.5l3.5 2 3.5-2-3.5-2z' stroke='var(--text-secondary)' strokeWidth='1' strokeLinecap='round' strokeLinejoin='round'/>
      )}
    </svg>
  );
}

export default function TopBar({ sessionName, onNewSession, onRequestLocation, locationLoading = false, locationError = null }: TopBarProps) {
  const t = useT('layout.topbar');
  // leftPanelOpen 的客户端初值依赖视口宽度（窄屏为 false），SSR 恒为 true：
  // hydrate 前按服务端默认值渲染折叠按钮，避免 390px 下 #418 hydration 失败。
  const hydrated = useHydrated();
  const storeLeftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const leftPanelOpen = hydrated ? storeLeftPanelOpen : true;
  const toggleLeftPanel = useHudStore((s) => s.toggleLeftPanel);
  const aiStatus = useHudStore((s) => s.aiStatus);
  const setSettingsOpen = useHudStore((s) => s.setSettingsOpen);
  const setHistoryOpen = useHudStore((s) => s.setHistoryOpen);
  const setTweaksOpen = useHudStore((s) => s.setTweaksOpen);
  const is3D = useHudStore((s) => s.is3D);
  const setIs3D = useHudStore((s) => s.setIs3D);
  const layoutMode = useLayoutMode();

  // Keep New session and Settings directly reachable in the mobile shell.
  const compactActions = layoutMode === 'mobile';
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const moreTriggerRef = useRef<HTMLButtonElement>(null);

  // 「更多」收纳菜单：Escape 关闭 + 点击外部关闭（与 BaselayerSwitcher 同模式）。
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMoreOpen(false);
        moreTriggerRef.current?.focus();
      }
    };
    const onMouseDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onMouseDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, [moreOpen]);

  const isActive = aiStatus === 'thinking' || aiStatus === 'acting';
  const name = sessionName ?? t('unnamed');

  const getStatusConfig = (status: string) => {
    switch (status) {
      case 'idle': return { label: t('statusReady'), color: 'var(--text-muted)', bg: 'var(--surface-sunken)' };
      case 'thinking': case 'acting': return { label: status === 'thinking' ? t('statusThinking') : t('statusActing'), color: 'var(--agent-accent)', bg: 'color-mix(in srgb, var(--agent-accent) 8%, transparent)' };
      // V4：走与 StatusBadge / InlineNotice / toast 同一套语义 token，
      // 不再各写一对明暗 hex（原先是 #4ade80/#16a34a 与 #fca5a5/#ef4444）。
      case 'done': return { label: t('statusDone'), color: 'var(--success)', bg: 'var(--success-soft)' };
      case 'error': return { label: t('statusError'), color: 'var(--critical)', bg: 'var(--critical-soft)' };
      default: return { label: t('statusReady'), color: 'var(--text-muted)', bg: 'var(--surface-sunken)' };
    }
  };

  const status = getStatusConfig(aiStatus);

  /* scan-line position 0-100%（prefers-reduced-motion 下不启动 rAF） */
  const scanRef = useRef<HTMLDivElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  useEffect(() => {
    if (!isActive || reducedMotion) return;
    let frame: number;
    let start: number | null = null;
    const DURATION = 2000;
    const tick = (ts: number) => {
      if (start === null) start = ts;
      const progress = ((ts - start) % DURATION) / DURATION;
      if (scanRef.current) scanRef.current.style.transform = `translateX(${progress * 250}%)`;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [isActive, reducedMotion]);

  return (
    // V4：壳层度量改用 token（h-topbar），背景改为不透明 surface-panel 并
    // 移除 backdrop-blur —— 顶栏压在持续重绘的地图画布上，blur 是最贵的
    // 那一类滤镜，半透明又会让地图细节透进密集文本。
    <header
      data-testid="topbar"
      className="fixed inset-x-0 top-0 z-50 flex h-topbar items-center gap-1 bg-surface-panel px-1.5 sm:gap-2 sm:px-3"
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
            ref={scanRef}
            style={{
              background: 'linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--agent-accent) 60%, transparent) 50%, transparent 100%)',
              width: '40%',
              transform: 'translateX(0)',
              height: '100%'
            }}
          />
        </div>
      )}

      {/* sidebar toggle */}
      <button
        type="button"
        onClick={toggleLeftPanel}
        aria-expanded={leftPanelOpen}
        aria-label={leftPanelOpen ? t('collapseSidebar') : t('expandSidebar')}
        title={leftPanelOpen ? t('collapseSidebar') : t('expandSidebar')}
        className={iconButton}
      >
        {leftPanelOpen ? <PanelLeftClose size={16} aria-hidden /> : <Menu size={16} aria-hidden />}
      </button>

      <div className="grid min-w-0 flex-1 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-1.5 min-[769px]:flex min-[769px]:gap-3">
        <span className="flex shrink-0 items-center gap-2 text-heading font-semibold leading-tight text-ink">
          <span aria-hidden className="hidden h-6 w-6 items-center justify-center rounded-md bg-agent-accent text-ink-on-accent min-[480px]:flex">
            <Compass size={16} />
          </span>
          GeoAgent
        </span>
        <span className="col-span-2 row-start-2 min-w-0 truncate text-caption text-ink-secondary min-[769px]:border-l min-[769px]:border-edge-subtle min-[769px]:pl-3 min-[769px]:text-body" title={t('sessionPrefix', { name })}>
          <span className="sr-only">{t('sessionPrefix', { name })}</span>
          <span aria-hidden>{name}</span>
        </span>
        <span
          title={status.label}
          className="col-start-2 row-start-1 inline-flex min-w-0 max-w-full items-center justify-self-start gap-1 rounded-pill px-1.5 text-caption font-medium min-[769px]:ml-auto min-[769px]:shrink-0 min-[769px]:px-2 min-[769px]:py-0.5"
          style={{ backgroundColor: status.bg }}
        >
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-pill" style={{ backgroundColor: status.color }} />
          <span className="truncate text-ink">{status.label}</span>
        </span>
      </div>

      {onRequestLocation && <>
        <button type="button" onClick={onRequestLocation} disabled={locationLoading}
          title={t(locationLoading ? 'locationLoading' : locationError ? 'locationRetry' : 'requestLocation')}
          aria-label={t(locationLoading ? 'locationLoading' : locationError ? 'locationRetry' : 'requestLocation')}
          aria-busy={locationLoading} aria-describedby={locationError ? 'location-error' : undefined}
          className={`${iconButton} disabled:opacity-60`}>
          {locationLoading ? <LoaderCircle size={14} className="animate-spin" aria-hidden /> : <LocateFixed size={14} aria-hidden />}
        </button>
        <span role="status" className="sr-only">{locationLoading ? t('locationLoading') : ''}</span>
        {locationError && <div id="location-error" role="alert"
          className="fixed left-4 right-4 top-14 z-[110] mx-auto max-w-sm rounded-md border border-status-warning-border bg-surface-raised p-3 text-body text-ink shadow-lg">
          <p>{t(`locationErrors.${locationError}`)}</p>
          <button type="button" onClick={onRequestLocation} className="mt-2 whitespace-nowrap rounded border border-edge-subtle px-3 py-1 text-ink-secondary">{t('locationRetry')}</button>
        </div>}
      </>}
      {/* right actions */}
      <div data-testid="topbar-actions" className="flex shrink-0 items-center gap-0.5 sm:gap-1">
        <button
          type="button"
          onClick={onNewSession}
          aria-label={t('newSession')}
          title={t('newSession')}
          className="inline-flex h-8 min-w-8 shrink-0 items-center justify-center gap-1.5 rounded-md border border-edge-subtle bg-surface-sunken text-body font-medium text-ink transition-colors hover:bg-surface-hover sm:h-9 sm:min-w-9 lg:px-2.5"
        >
          <Plus size={16} aria-hidden />
          <span className="hidden lg:inline">{t('newSession')}</span>
        </button>

        {/* #1667：窄档折叠 —— 历史/底图/3D/UI 调整收进「更多」收纳菜单，
            设置保持内联（390px 下验收的命中入口）。 */}
        {compactActions ? (
          <div ref={moreRef} className="relative">
            <button
              type="button"
              ref={moreTriggerRef}
              onClick={() => setMoreOpen((v) => !v)}
              aria-label={t('moreActions')}
              title={t('moreActions')}
              aria-haspopup="menu"
              aria-expanded={moreOpen}
              className={iconButton}
            >
              <MoreHorizontal size={14} aria-hidden />
            </button>
            {moreOpen && (
              <div
                role="menu"
                aria-label={t('moreActions')}
                className="absolute right-0 top-full z-30 mt-1 min-w-[184px] rounded-md border border-edge-subtle bg-surface-panel py-1 shadow-overlay"
              >
                <button
                  role="menuitem"
                  onClick={() => {
                    setHistoryOpen(true);
                    setMoreOpen(false);
                  }}
                  className={menuItem}
                >
                  <History size={14} aria-hidden />
                  {t('history')}
                </button>
                {/* 底图行：BaselayerSwitcher 自带 listbox 浮层与 aria-label
                    （含当前底图名）—— 收纳菜单内嵌一个受管浮层。ARIA 上 menu
                    的直属子元素应为 menuitem，故该行包一层 role="none" 承载
                    非 menuitem 控件；Escape/外部点击由其自身监听 + moreRef 兜底。 */}
                <div role="none" className="px-3 py-1.5">
                  <BaselayerSwitcher />
                </div>
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => {
                    setIs3D(!is3D);
                    setMoreOpen(false);
                  }}
                  aria-label={is3D ? t('switchTo2D') : t('switchTo3D')}
                  className={menuItem}
                >
                  <ViewToggleGlyph is3D={is3D} />
                  <span
                    className="font-mono"
                    style={{ color: is3D ? 'var(--success)' : undefined }}
                  >
                    {is3D ? '3D' : '2D'}
                  </span>
                </button>
                <button
                  role="menuitem"
                  onClick={() => {
                    setTweaksOpen(true);
                    setMoreOpen(false);
                  }}
                  className={menuItem}
                >
                  <Sliders size={14} aria-hidden />
                  {t('uiTweaks')}
                </button>
              </div>
            )}
          </div>
        ) : (
          <>
            <button
              onClick={() => setHistoryOpen(true)}
              aria-label={t('history')}
              title={t('history')}
              className={iconButton}
            >
              <History size={14} aria-hidden />
            </button>

            <span aria-hidden className="mx-1 h-4 w-px bg-edge-subtle" />

            <BaselayerSwitcher />

            <button
              type='button'
              onClick={() => setIs3D(!is3D)}
              aria-label={is3D ? t('switchTo2D') : t('switchTo3D')}
              title={is3D ? t('view3DTitle') : t('view2DTitle')}
              className="flex h-8 items-center gap-1 rounded-md px-2 font-mono text-body text-ink-secondary transition-colors hover:bg-surface-hover"
            >
              <ViewToggleGlyph is3D={is3D} />
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
              aria-label={t('uiTweaks')}
              title={t('uiTweaks')}
              className={iconButton}
            >
              <Sliders size={14} aria-hidden />
            </button>
          </>
        )}

        <button
          onClick={() => setSettingsOpen(true)}
          aria-label={t('settings')}
          title={t('settings')}
          className={iconButton}
        >
          <Settings size={14} aria-hidden />
        </button>
      </div>
    </header>
  );
}
