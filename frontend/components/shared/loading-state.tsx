'use client';

/**
 * LoadingState — 统一加载占位（UI V3 shared primitive）。
 * 替代各处发散的“正在加载…”裸文本。
 */
import { Loader2 } from 'lucide-react';

export function LoadingState({ label = '正在加载…' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 px-4 py-10 text-[12px] text-[var(--theme-text-muted)]">
      <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export default LoadingState;
