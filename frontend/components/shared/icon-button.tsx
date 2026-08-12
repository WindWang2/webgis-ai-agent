'use client';

/**
 * IconButton — 统一图标按钮（UI V3 shared primitive）。
 *
 * - `label` 必填：同时作为 aria-label 与 title（tooltip），杜绝无名字图标按钮。
 * - 28×28、主题变量着色、hover 有底色；`active` 时用 accent 色。
 */
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';
import clsx from 'clsx';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> {
  label: string;
  icon: LucideIcon;
  /** 图标 px 尺寸，默认 15 */
  iconSize?: number;
  active?: boolean;
  /** danger：hover 呈红色（删除类操作） */
  variant?: 'ghost' | 'danger';
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon: Icon, iconSize = 15, active = false, variant = 'ghost', className, style, type, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-label={label}
      title={label}
      aria-pressed={active || undefined}
      className={clsx(
        'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors',
        variant === 'danger' ? 'hover:bg-red-500/10 hover:text-red-500' : 'hover:bg-[var(--theme-bg-hover)]',
        className
      )}
      style={{
        color: active
          ? 'var(--agent-accent, #16a34a)'
          : variant === 'danger'
            ? 'var(--theme-text-muted)'
            : 'var(--theme-text-secondary)',
        ...style,
      }}
      {...rest}
    >
      <Icon size={iconSize} strokeWidth={active ? 2.2 : 1.7} aria-hidden />
    </button>
  );
});

export default IconButton;
