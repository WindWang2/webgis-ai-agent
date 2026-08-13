'use client';

/**
 * LoadingState — 统一加载占位（UI V3 shared primitive）。
 * 替代各处发散的“正在加载…”裸文本。
 *
 * UI V4：垂直节奏与图标尺寸对齐 EmptyState（审计：两者曾是 py-10/py-10 但
 * 图标 18px vs 14px、文字 13px vs 12px，并排出现时明显不成套）。
 */
import { Loader2 } from 'lucide-react';
import { ACCENT_TILE_CLASS } from './panel-header';

export function LoadingState({ label = '正在加载…' }: { label?: string }) {
  return (
    <div
      role="status"
      className="flex flex-col items-center justify-center px-6 py-10 text-center"
    >
      <span aria-hidden className={`${ACCENT_TILE_CLASS} mb-3 h-control-lg w-control-lg`}>
        <Loader2 size={16} className="animate-spin text-status-accent motion-reduce:animate-none" />
      </span>
      <p className="text-body font-medium text-ink-secondary">{label}</p>
    </div>
  );
}

export default LoadingState;
