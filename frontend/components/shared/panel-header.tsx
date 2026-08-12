'use client';

/**
 * PanelHeader — 统一 context panel 头部（UI V3 shared primitive）。
 *
 * 替代各 tab 自建的三套发散 header（审计：p-3 大写 / px-3 py-2 / 英文自有样式）。
 * 组成：icon tile + title/description + badge + 右侧 contextual actions + close。
 *
 * UI V4：全部改用语义 token（text-title / text-meta / surface-*），并与
 * EmptyState 共用同一个 accent icon tile 配方 —— 审计发现两处 tile 的 accent
 * 透明度分别是 12% 与 10%，肉眼可辨的不一致。
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

/** accent 图标底座 —— PanelHeader 与 EmptyState 共用，保证同一视觉配方。 */
export const ACCENT_TILE_CLASS =
  'flex shrink-0 items-center justify-center rounded-md bg-status-accent-soft';

export function PanelHeader({ icon: Icon, title, description, badge, actions, onClose, id }: PanelHeaderProps) {
  const showBadge = badge !== undefined && badge !== 0 && badge !== '';
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-edge-subtle bg-surface-panel px-panel py-2">
      {Icon && (
        <span aria-hidden className={`${ACCENT_TILE_CLASS} h-control-md w-control-md`}>
          <Icon size={14} className="text-status-accent" />
        </span>
      )}
      <div className="min-w-0 flex-1 leading-tight">
        <div className="flex items-center gap-1.5">
          <h2 id={id} className="truncate text-title font-semibold text-ink">
            {title}
          </h2>
          {showBadge && (
            <span className="inline-flex h-4 min-w-[16px] items-center justify-center rounded-pill bg-surface-sunken px-1 text-micro font-semibold tabular-nums text-ink-secondary">
              {badge}
            </span>
          )}
        </div>
        {description && <p className="mt-0.5 truncate text-meta text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-0.5">{actions}</div>}
      {onClose && <IconButton label="收起面板" icon={X} onClick={onClose} />}
    </div>
  );
}

export default PanelHeader;
