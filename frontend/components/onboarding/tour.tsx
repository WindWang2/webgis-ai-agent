'use client';

import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { usePrefersReducedMotion } from '@/lib/hooks/use-prefers-reduced-motion';
import { useOnboardingStore } from '@/lib/onboarding/use-onboarding';
import { TOUR_STEPS } from './tour-steps';

/**
 * 首次运行引导（ADR-0147 P6）。
 *
 * - 步骤高亮：目标矩形外圈「焦点圈闭」（fixed 遮罩 + box-shadow 挖孔），
 *   键盘可达（←/→/Enter 步进、Esc 跳过），焦点圈闭经 useDialogFocus；
 * - 目标缺失降级为居中卡片（tour 永不卡死）；
 * - reduced-motion：脉冲动画与位移动画全部关闭（双轨约定的 JS 侧）。
 */

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

function measureTarget(selector?: string): Rect | null {
  if (!selector || typeof document === 'undefined') return null;
  try {
    const el = document.querySelector<HTMLElement>(selector);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    return { top: r.top, left: r.left, width: r.width, height: r.height };
  } catch {
    return null;
  }
}

const SPOT_PAD = 8;

export function Tour(): React.ReactElement | null {
  const tourOpen = useOnboardingStore((s) => s.tourOpen);
  const stepIndex = useOnboardingStore((s) => s.stepIndex);
  const nextStep = useOnboardingStore((s) => s.nextStep);
  const prevStep = useOnboardingStore((s) => s.prevStep);
  const finishTour = useOnboardingStore((s) => s.finishTour);
  const reducedMotion = usePrefersReducedMotion();

  const step = TOUR_STEPS[stepIndex];
  const [rect, setRect] = useState<Rect | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useDialogFocus({
    open: tourOpen,
    containerRef,
    onEscape: () => finishTour(false),
    initialFocusSelector: '[data-tour-next]',
  });

  // 目标测量（布局/滚动变化后重测；找不到降级居中）
  useEffect(() => {
    if (!tourOpen) return;
    const update = () => setRect(measureTarget(step?.targetSelector));
    update();
    const t = setTimeout(update, 60);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      clearTimeout(t);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [tourOpen, step?.targetSelector, stepIndex]);

  // 键盘步进
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'Enter') {
      e.preventDefault();
      nextStep(TOUR_STEPS.length);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      prevStep();
    }
  };

  if (!tourOpen || !step) return null;

  const spot = rect
    ? {
        top: rect.top - SPOT_PAD,
        left: rect.left - SPOT_PAD,
        width: rect.width + SPOT_PAD * 2,
        height: rect.height + SPOT_PAD * 2,
      }
    : null;

  // 卡片位置：优先贴目标下方，放不下贴上方，无目标居中
  let cardStyle: React.CSSProperties = {};
  if (spot) {
    const below = spot.top + spot.height + 12;
    cardStyle =
      below + 220 < window.innerHeight
        ? { top: below, left: Math.max(12, Math.min(spot.left, window.innerWidth - 400)) }
        : { top: Math.max(12, spot.top - 232), left: Math.max(12, Math.min(spot.left, window.innerWidth - 400)) };
  }

  return (
    <div className="fixed inset-0 z-[95]" onKeyDown={onKeyDown} data-testid="onboarding-tour">
      {/* 遮罩 + 焦点圈闭（有目标时挖孔，无目标时整层轻遮罩） */}
      <div
        className="absolute inset-0 bg-black/50"
        style={
          spot
            ? {
                boxShadow: `0 0 0 9999px rgba(0,0,0,0.5)`,
                clipPath: 'none',
                ...(reducedMotion ? {} : { transition: 'all 200ms ease' }),
              }
            : undefined
        }
        onMouseDown={() => finishTour(false)}
        aria-hidden
      />
      {spot ? (
        <div
          className={`pointer-events-none absolute rounded-md ring-2 ring-status-accent ${
            reducedMotion ? '' : 'animate-pulse'
          }`}
          style={{ top: spot.top, left: spot.left, width: spot.width, height: spot.height }}
          data-testid="tour-spotlight"
        />
      ) : null}

      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={`新手引导：${step.title}`}
        data-testid="tour-card"
        className={`absolute w-[380px] max-w-[calc(100vw-24px)] rounded-lg border border-edge-subtle bg-surface-raised p-4 shadow-2xl ${
          spot ? '' : 'left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2'
        }`}
        style={cardStyle}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-caption font-medium uppercase tracking-wide text-status-info">
              第 {stepIndex + 1} / {TOUR_STEPS.length} 步
            </p>
            <h2 className="text-body-md font-semibold text-ink">{step.title}</h2>
          </div>
          <button
            type="button"
            aria-label="跳过引导"
            onClick={() => finishTour(false)}
            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
          >
            <X size={14} aria-hidden />
          </button>
        </div>
        <p className="mt-2 text-body-sm text-ink-secondary">{step.body}</p>
        <div className="mt-3 flex items-center justify-between">
          <div className="flex gap-1" aria-hidden>
            {TOUR_STEPS.map((s, i) => (
              <span
                key={s.id}
                className={`h-1.5 w-1.5 rounded-full ${i === stepIndex ? 'bg-status-accent' : 'bg-surface-sunken'}`}
              />
            ))}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={prevStep}
              disabled={stepIndex === 0}
              className="rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:opacity-40"
            >
              上一步
            </button>
            <button
              type="button"
              data-tour-next
              onClick={() => nextStep(TOUR_STEPS.length)}
              className="rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-semibold text-ink-on-accent hover:opacity-90"
            >
              {stepIndex === TOUR_STEPS.length - 1 ? '完成' : '下一步'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
