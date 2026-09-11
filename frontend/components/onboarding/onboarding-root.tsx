'use client';

import React, { useEffect } from 'react';
import { useOnboardingStore } from '@/lib/onboarding/use-onboarding';
import { Tour } from './tour';
import { HintQueue } from './hint-queue';

/**
 * Onboarding 挂载根（ADR-0147 P6）：
 * - hydrate 持久化状态；
 * - 首次运行（tourSeen=false）延迟 1.2s 自动启动 tour（等布局与地图壳稳定）；
 * - 渲染 Tour 与 HintQueue。重看/重置入口在 设置 → 系统。
 */
export function OnboardingRoot(): React.ReactElement | null {
  const hydrate = useOnboardingStore((s) => s.hydrate);
  const tourSeen = useOnboardingStore((s) => s.tourSeen);
  const startTour = useOnboardingStore((s) => s.startTour);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (tourSeen) return;
    const t = setTimeout(() => startTour(), 1200);
    return () => clearTimeout(t);
  }, [tourSeen, startTour]);

  return (
    <>
      <Tour />
      <HintQueue />
    </>
  );
}
