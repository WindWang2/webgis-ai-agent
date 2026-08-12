'use client';

/**
 * EmptyState — 统一空状态（UI V3 shared primitive）。
 *
 * 收敛审计发现的 4+ 种发散空状态（hero / 裸文本行 / 斜体盒）。
 * 可选 action 用于跨面板引导（如 图层空 → 前往数据源）。
 *
 * UI V4：icon tile 与 PanelHeader/LoadingState 共用同一配方，文字落到 token 刻度。
 */
import type { LucideIcon } from 'lucide-react';
import { ACCENT_TILE_CLASS } from './panel-header';

export interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <span aria-hidden className={`${ACCENT_TILE_CLASS} mb-3 h-control-lg w-control-lg`}>
        <Icon size={16} className="text-status-accent" />
      </span>
      <p className="text-body font-medium text-ink-secondary">{title}</p>
      {description && <p className="mt-1 text-meta text-ink-muted">{description}</p>}
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-3 rounded-sm bg-status-accent px-3 py-1 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-85"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

export default EmptyState;
