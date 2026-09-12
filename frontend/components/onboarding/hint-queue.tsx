'use client';

import React, { useEffect } from 'react';
import { Lightbulb, X } from 'lucide-react';
import { useOnboardingStore } from '@/lib/onboarding/use-onboarding';
import { HINTS } from './tour-steps';

/**
 * 上下文提示队列（ADR-0147 P6）：低打扰、每条只出现一次、可重置。
 *
 * 展示策略：tour 看完后启动；同一时刻至多一张卡；出现 12s 自动换下一条
 * （视为已读）或手动关闭/「下一条」。所有卡都看过即静默（设置 → 系统 可重置）。
 */

const HINT_ROTATE_MS = 12_000;

export function HintQueue(): React.ReactElement | null {
  const tourSeen = useOnboardingStore((s) => s.tourSeen);
  const tourOpen = useOnboardingStore((s) => s.tourOpen);
  const hintsSeen = useOnboardingStore((s) => s.hintsSeen);
  const activeHintId = useOnboardingStore((s) => s.activeHintId);
  const setActiveHint = useOnboardingStore((s) => s.setActiveHint);
  const markHintSeen = useOnboardingStore((s) => s.markHintSeen);

  const unseen = HINTS.filter((h) => !hintsSeen.includes(h.id));

  // 取下一条未读提示
  useEffect(() => {
    if (!tourSeen || tourOpen || activeHintId !== null || unseen.length === 0) return;
    const t = setTimeout(() => setActiveHint(unseen[0].id), 1500);
    return () => clearTimeout(t);
  }, [tourSeen, tourOpen, activeHintId, unseen, setActiveHint]);

  // 当前卡自动轮换（视为已读）
  useEffect(() => {
    if (!activeHintId) return;
    const t = setTimeout(() => markHintSeen(activeHintId), HINT_ROTATE_MS);
    return () => clearTimeout(t);
  }, [activeHintId, markHintSeen]);

  if (!tourSeen || tourOpen || !activeHintId) return null;
  const hint = HINTS.find((h) => h.id === activeHintId);
  if (!hint) return null;

  return (
    <div
      className="fixed bottom-4 left-4 z-[70] w-[320px] rounded-lg border border-edge-subtle bg-surface-raised p-3 shadow-xl"
      role="status"
      data-testid="onboarding-hint"
    >
      <div className="flex items-start gap-2">
        <Lightbulb size={14} aria-hidden className="mt-0.5 shrink-0 text-status-warning" />
        <p className="min-w-0 flex-1 text-caption text-ink-secondary">{hint.text}</p>
        <button
          type="button"
          aria-label="关闭提示"
          onClick={() => markHintSeen(hint.id)}
          className="rounded-sm p-0.5 text-ink-muted hover:bg-surface-hover hover:text-ink"
        >
          <X size={12} aria-hidden />
        </button>
      </div>
      <div className="mt-1.5 flex items-center justify-between">
        <span className="text-caption text-ink-muted">
          提示 {hintsSeen.length + 1}/{HINTS.length}
        </span>
        <button
          type="button"
          onClick={() => markHintSeen(hint.id)}
          className="rounded-sm px-1.5 py-0.5 text-caption text-status-accent hover:bg-surface-hover"
        >
          下一条
        </button>
      </div>
    </div>
  );
}
