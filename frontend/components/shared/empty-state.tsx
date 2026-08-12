'use client';

/**
 * EmptyState — 统一空状态（UI V3 shared primitive）。
 *
 * 收敛审计发现的 4+ 种发散空状态（hero / 裸文本行 / 斜体盒）。
 * 可选 action 用于跨面板引导（如 图层空 → 前往数据源）。
 */
import type { LucideIcon } from 'lucide-react';

export interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <span
        aria-hidden
        className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl"
        style={{ background: 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)' }}
      >
        <Icon size={18} style={{ color: 'var(--agent-accent, #16a34a)' }} />
      </span>
      <p className="text-[13px] font-medium text-[var(--theme-text-secondary)]">{title}</p>
      {description && <p className="mt-1 text-[12px] leading-relaxed text-[var(--theme-text-muted)]">{description}</p>}
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-3 rounded-md px-3 py-1.5 text-[12px] font-medium text-white transition-opacity hover:opacity-85"
          style={{ background: 'var(--agent-accent, #16a34a)' }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

export default EmptyState;
