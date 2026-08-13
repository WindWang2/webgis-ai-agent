'use client';

/**
 * InlineNotice — 统一内联提示条（UI V3 shared primitive）。
 *
 * error 使用 role=alert（断言式），其余 role=status（礼貌播报）。
 *
 * UI V4：配色改为复用 StatusBadge 的同一批语义 token，因此提示条与徽标在同屏
 * 出现时是同一套颜色语言（此前两者各写一套 Tailwind 调色板 + dark: 变体）。
 */
import type { ReactNode } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import clsx from 'clsx';
import { STATUS_TONE, type StatusTone } from './status-badge';

export type InlineNoticeVariant = 'error' | 'warning' | 'info' | 'success';

const VARIANT: Record<InlineNoticeVariant, { tone: StatusTone; Icon: typeof Info }> = {
  error: { tone: 'critical', Icon: AlertCircle },
  warning: { tone: 'warning', Icon: AlertTriangle },
  info: { tone: 'info', Icon: Info },
  success: { tone: 'success', Icon: CheckCircle2 },
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
  const { tone, Icon } = VARIANT[variant];
  return (
    <div
      role={variant === 'error' ? 'alert' : 'status'}
      className={clsx(
        'flex items-start gap-2 rounded-sm border px-2 py-1.5 text-meta',
        STATUS_TONE[tone],
        className
      )}
    >
      <Icon size={14} className="mt-px shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export default InlineNotice;
