'use client';

/**
 * InlineNotice — 统一内联提示条（UI V3 shared primitive）。
 *
 * error 使用 role=alert（断言式），其余 role=status（礼貌播报）。
 * 颜色全部 dark-safe（Tailwind dark: 变体）。
 */
import type { ReactNode } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import clsx from 'clsx';

export type InlineNoticeVariant = 'error' | 'warning' | 'info' | 'success';

const VARIANT_STYLE: Record<InlineNoticeVariant, { className: string; Icon: typeof Info }> = {
  error: {
    className: 'border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-300',
    Icon: AlertCircle,
  },
  warning: {
    className: 'border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-300',
    Icon: AlertTriangle,
  },
  info: {
    className: 'border-sky-500/25 bg-sky-500/10 text-sky-600 dark:text-sky-300',
    Icon: Info,
  },
  success: {
    className: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
    Icon: CheckCircle2,
  },
};

export function InlineNotice({
  variant,
  children,
  className,
}: {
  variant: InlineNoticeVariant;
  children: ReactNode;
  className?: string;
}) {
  const { className: variantClass, Icon } = VARIANT_STYLE[variant];
  return (
    <div
      role={variant === 'error' ? 'alert' : 'status'}
      className={clsx('flex items-start gap-2 rounded-md border px-2.5 py-2 text-[12px] leading-relaxed', variantClass, className)}
    >
      <Icon size={14} className="mt-0.5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export default InlineNotice;
