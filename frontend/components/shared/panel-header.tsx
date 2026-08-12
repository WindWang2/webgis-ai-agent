'use client';

/**
 * PanelHeader — 统一 context panel 头部（UI V3 shared primitive）。
 *
 * 替代各 tab 自建的三套发散 header（审计：p-3 大写 / px-3 py-2 / 英文自有样式）。
 * 组成：icon tile + title/description + badge + 右侧 contextual actions + close。
 */
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { X } from 'lucide-react';
import { IconButton } from './icon-button';

export interface PanelHeaderProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  /** 计数徽标（如图层数）；0/undefined 不渲染 */
  badge?: number | string;
  /** 右侧上下文操作（IconButton 组） */
  actions?: ReactNode;
  onClose?: () => void;
  /** 头部唯一 id，供 tabpanel aria-labelledby 关联 */
  id?: string;
}

export function PanelHeader({ icon: Icon, title, description, badge, actions, onClose, id }: PanelHeaderProps) {
  const showBadge = badge !== undefined && badge !== 0 && badge !== '';
  return (
    <div
      className="flex shrink-0 items-center gap-2.5 border-b border-[var(--theme-border)] px-3 py-2.5"
      style={{ background: 'var(--theme-bg-glass)' }}
    >
      {Icon && (
        <span
          aria-hidden
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
          style={{ background: 'color-mix(in srgb, var(--agent-accent, #16a34a) 12%, transparent)' }}
        >
          <Icon size={15} style={{ color: 'var(--agent-accent, #16a34a)' }} />
        </span>
      )}
      <div className="min-w-0 flex-1 leading-tight">
        <div className="flex items-center gap-1.5">
          <h2 id={id} className="truncate text-[14px] font-semibold text-[var(--theme-text-primary)]">
            {title}
          </h2>
          {showBadge && (
            <span className="inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-[var(--theme-bg-muted)] px-1 text-[10px] font-semibold text-[var(--theme-text-secondary)]">
              {badge}
            </span>
          )}
        </div>
        {description && (
          <p className="mt-0.5 truncate text-[12px] text-[var(--theme-text-muted)]">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-0.5">{actions}</div>}
      {onClose && <IconButton label="收起面板" icon={X} onClick={onClose} />}
    </div>
  );
}

export default PanelHeader;
