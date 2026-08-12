'use client';

/**
 * ConfirmAction — 两段式行内确认按钮（UI V3 shared primitive）。
 *
 * 把 map-studio 的 2-step confirm + 3s 自动还原 + blur 取消泛化，
 * 替代原生 confirm()（project/data-sources）与无确认删除（ops-log）。
 * 危险操作默认红色语义。
 */
import { useEffect, useRef, useState, type ButtonHTMLAttributes } from 'react';
import clsx from 'clsx';

export interface ConfirmActionProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  /** 初始文案（如“删除”） */
  label: string;
  /** 确认态文案（如“确认删除？”） */
  confirmLabel?: string;
  onConfirm: () => void;
  /** 自动还原毫秒，默认 3000 */
  timeoutMs?: number;
}

export function ConfirmAction({
  label,
  confirmLabel = '确认？',
  onConfirm,
  timeoutMs = 3000,
  className,
  ...rest
}: ConfirmActionProps) {
  const [confirming, setConfirming] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!confirming) return;
    timerRef.current = setTimeout(() => setConfirming(false), timeoutMs);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [confirming, timeoutMs]);

  return (
    <button
      type="button"
      aria-label={confirming ? confirmLabel : label}
      className={clsx(
        'rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
        confirming
          ? 'bg-red-500/15 text-red-600 hover:bg-red-500/25 dark:text-red-300'
          : 'text-[var(--theme-text-muted)] hover:bg-red-500/10 hover:text-red-500',
        className
      )}
      onClick={(e) => {
        e.stopPropagation();
        if (confirming) {
          setConfirming(false);
          onConfirm();
        } else {
          setConfirming(true);
        }
      }}
      onBlur={() => setConfirming(false)}
      {...rest}
    >
      {confirming ? confirmLabel : label}
    </button>
  );
}

export default ConfirmAction;
