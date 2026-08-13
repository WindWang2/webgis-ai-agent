'use client';

/**
 * IconButton — 统一图标按钮（UI V3 shared primitive）。
 *
 * - `label` 必填：同时作为 aria-label 与 title（tooltip），杜绝无名字图标按钮。
 * - 主题变量着色、hover 有底色；`active` 时用 accent 色。
 *
 * UI V4 三处收敛：
 * 1. `size` 取代逐处手写的 h-6/h-7/h-8。dense 列表用 `sm`：审计发现图层行的
 *    59px 行高其实是被两颗 28px 操作按钮撑起来的（图标本身只有 12px）。
 * 2. 补上 disabled 视觉 —— 之前 disabled 与 enabled 完全同貌。
 * 3. 图标尺寸落到 --icon-* 三档，替代原先 12 个散落的 px 值。
 */
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';
import clsx from 'clsx';

export type IconButtonSize = 'sm' | 'md' | 'lg';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> {
  label: string;
  icon: LucideIcon;
  /** 图标 px 尺寸；默认跟随 size */
  iconSize?: number;
  size?: IconButtonSize;
  active?: boolean;
  /** danger：hover 呈红色（删除类操作） */
  variant?: 'ghost' | 'danger';
}

const SIZE_CLASS: Record<IconButtonSize, string> = {
  sm: 'h-control-sm w-control-sm',
  md: 'h-control-md w-control-md',
  lg: 'h-control-lg w-control-lg',
};

const DEFAULT_ICON_PX: Record<IconButtonSize, number> = { sm: 12, md: 14, lg: 16 };

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon: Icon, iconSize, size = 'md', active = false, variant = 'ghost', className, type, disabled, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-label={label}
      title={label}
      aria-pressed={active || undefined}
      disabled={disabled}
      className={clsx(
        'inline-flex shrink-0 items-center justify-center rounded-sm transition-colors',
        SIZE_CLASS[size],
        active ? 'bg-status-accent-soft text-status-accent' : 'text-ink-secondary',
        variant === 'danger' && !active && 'text-ink-muted',
        !disabled && variant === 'danger' && 'hover:bg-status-critical-soft hover:text-status-critical',
        !disabled && variant !== 'danger' && !active && 'hover:bg-surface-hover hover:text-ink',
        disabled && 'cursor-not-allowed text-ink-disabled hover:bg-transparent',
        className
      )}
      {...rest}
    >
      <Icon size={iconSize ?? DEFAULT_ICON_PX[size]} strokeWidth={active ? 2.2 : 1.7} aria-hidden />
    </button>
  );
});

export default IconButton;
