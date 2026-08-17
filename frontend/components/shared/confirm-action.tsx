'use client';

/**
 * ConfirmAction — 两段式行内确认按钮（UI V3 shared primitive）。
 *
 * 把 map-studio 的 2-step confirm + 3s 自动还原 + blur 取消泛化，
 * 替代原生 confirm()（project/data-sources）与无确认删除（ops-log）。
 * 危险操作默认红色语义。
 *
 * UI V4：配色改用 --critical token，与 StatusBadge / InlineNotice 的
 * critical 语义同源。
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
  'aria-label': ariaLabel,
  className,
  ...rest
}: ConfirmActionProps) {
  const [confirming, setConfirming] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Review P2 修复：双击会在 250ms 内连点两下直接完成“arm+confirm”，
  // 两段式保护形同虚设；确认点击必须发生在 arm 之后的最小间隔外。
  const armedAtRef = useRef(0);
  const MIN_ARM_MS = 250;

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
      // #553 审计：`{...rest}` 若展开在 computed aria-label 之后会覆盖它 ——
      // 调用方传了 aria-label 后，确认态下读屏听到的仍是初始名，两段式状态
      // 无从感知。这里把调用方 label 拆出来：确认态恒用 confirmLabel，
      // 非确认态优先调用方显式 aria-label，否则回退初始 label。
      aria-label={confirming ? confirmLabel : (ariaLabel ?? label)}
      className={clsx(
        'rounded-sm px-2 py-0.5 text-caption font-medium transition-colors',
        confirming
          ? 'bg-status-critical-soft text-status-critical hover:brightness-110'
          : 'text-ink-muted hover:bg-status-critical-soft hover:text-status-critical',
        className
      )}
      onClick={(e) => {
        e.stopPropagation();
        if (confirming) {
          if (Date.now() - armedAtRef.current < MIN_ARM_MS) return;
          setConfirming(false);
          onConfirm();
        } else {
          armedAtRef.current = Date.now();
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
