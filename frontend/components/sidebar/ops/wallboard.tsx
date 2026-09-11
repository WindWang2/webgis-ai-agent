'use client';

/**
 * 大屏值守模式（P7，ADR-0142 D1）—— 全屏轮播巡检面。
 *
 * - 轮播三视图：集群总览 → 断路器/缓存 → 系统健康；间隔 15s；
 * - 自动轮播**默认尊重 prefers-reduced-motion**（命中即默认关闭）；
 * - 键盘可达：←/→ 手动切页、Space 开/关自动轮播、F 全屏、Esc 退出；
 * - 全屏 API（不支持时优雅降级为普通覆盖层）；
 * - 高对比：全部走主题 token（明暗主题天然适配）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Maximize, Minimize, Pause, Play, X } from 'lucide-react';
import { ClusterDashboard } from './cluster-dashboard';
import { BreakerPanel } from './breaker-panel';
import { SystemHealthPanel } from './system-health-panel';

const VIEWS = [
  { key: 'cluster', label: '集群总览' },
  { key: 'breaker', label: '断路器 / 缓存' },
  { key: 'health', label: '系统健康' },
] as const;

const CAROUSEL_INTERVAL_MS = 15_000;

export function Wallboard({
  ownerToken,
  onExit,
}: {
  ownerToken?: string | null;
  onExit: () => void;
}) {
  const prefersReducedMotion = useRef(
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false,
  );
  const [auto, setAuto] = useState(!prefersReducedMotion.current);
  const [viewIdx, setViewIdx] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // context-panel 容器带 transform（滑入动画）—— position:fixed 会被收编为
  // 面板内定位，大屏必须 portal 到 body 才能真正覆盖全屏。
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const next = useCallback(() => setViewIdx((i) => (i + 1) % VIEWS.length), []);
  const prev = useCallback(() => setViewIdx((i) => (i - 1 + VIEWS.length) % VIEWS.length), []);

  // 自动轮播（可关；reduced-motion 默认关）。
  useEffect(() => {
    if (!auto) return;
    const timer = setInterval(next, CAROUSEL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [auto, next]);

  const toggleFullscreen = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    } else if (el.requestFullscreen) {
      void el.requestFullscreen().catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    const onFsChange = () => setIsFullscreen(document.fullscreenElement === containerRef.current);
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowRight':
          e.preventDefault();
          next();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          prev();
          break;
        case ' ':
          e.preventDefault();
          setAuto((v) => !v);
          break;
        case 'f':
        case 'F':
          e.preventDefault();
          toggleFullscreen();
          break;
        case 'Escape':
          if (!document.fullscreenElement) onExit();
          break;
      }
    },
    [next, prev, onExit, toggleFullscreen],
  );

  if (!mounted) return null;

  return createPortal(
    <div
      ref={containerRef}
      data-testid="ops-wallboard"
      role="region"
      aria-roledescription="轮播"
      aria-label="集群值守大屏"
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="fixed inset-0 z-[70] flex flex-col overflow-y-auto bg-surface-panel p-4 text-ink"
    >
      {/* 顶部条 */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-3">
          <h2 className="text-heading font-semibold">集群值守</h2>
          <span className="text-meta tabular-nums text-ink-muted" data-testid="wallboard-clock">
            {new Date().toLocaleTimeString('zh-CN', { hour12: false })}
          </span>
          <span className="text-micro text-ink-muted">
            {auto ? `自动轮播 ${CAROUSEL_INTERVAL_MS / 1000}s` : '手动模式'}
            {prefersReducedMotion.current && '（reduced-motion）'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            data-testid="wallboard-carousel-toggle"
            aria-pressed={auto}
            onClick={() => setAuto((v) => !v)}
            aria-label={auto ? '暂停自动轮播' : '开启自动轮播'}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-secondary hover:bg-surface-hover"
          >
            {auto ? <Pause size={15} aria-hidden /> : <Play size={15} aria-hidden />}
          </button>
          <button
            type="button"
            data-testid="wallboard-fullscreen"
            aria-pressed={isFullscreen}
            onClick={toggleFullscreen}
            aria-label={isFullscreen ? '退出全屏' : '进入全屏'}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-secondary hover:bg-surface-hover"
          >
            {isFullscreen ? <Minimize size={15} aria-hidden /> : <Maximize size={15} aria-hidden />}
          </button>
          <button
            type="button"
            data-testid="wallboard-exit"
            onClick={onExit}
            aria-label="退出值守大屏"
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-secondary hover:bg-surface-hover"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
      </div>

      {/* 分页指示 + 手动切换 */}
      <div role="tablist" aria-label="值守视图" className="mb-3 flex items-center gap-1">
        {VIEWS.map((v, i) => (
          <button
            key={v.key}
            type="button"
            role="tab"
            aria-selected={viewIdx === i}
            onClick={() => setViewIdx(i)}
            className={`rounded-sm px-2 py-1 text-meta font-medium transition-colors ${
              viewIdx === i
                ? 'bg-status-accent-soft text-status-accent'
                : 'text-ink-secondary hover:bg-surface-hover'
            }`}
          >
            {v.label}
          </button>
        ))}
      </div>

      {/* 当前视图 */}
      <div className="min-h-0 flex-1">
        {VIEWS[viewIdx].key === 'cluster' && (
          <ClusterDashboard ownerToken={ownerToken} variant="wallboard" />
        )}
        {VIEWS[viewIdx].key === 'breaker' && <BreakerPanel />}
        {VIEWS[viewIdx].key === 'health' && <SystemHealthPanel ownerToken={ownerToken} />}
      </div>

      <p className="mt-3 text-micro text-ink-muted">
        键盘：←/→ 切页 · Space 开关轮播 · F 全屏 · Esc 退出
      </p>
    </div>,
    document.body,
  );
}
