'use client';

/**
 * StatusBadge — 统一状态徽标（UI V3 shared primitive）。
 *
 * 覆盖任务中心 8 种 JobStatus（ADR-0052）与通用 active/done/error 语义；
 * dark-safe；running/cancelling 带克制的 pulse 点（reduced-motion 下自动关闭）。
 */
import clsx from 'clsx';

export interface StatusBadgeProps {
  status: string;
  /** 覆盖默认中文标签 */
  label?: string;
}

const STATUS_MAP: Record<string, { label: string; className: string; pulse?: boolean }> = {
  pending: { label: '等待中', className: 'border-slate-400/30 bg-slate-400/10 text-slate-500 dark:text-slate-300' },
  queued: { label: '排队中', className: 'border-slate-400/30 bg-slate-400/10 text-slate-500 dark:text-slate-300' },
  running: { label: '运行中', className: 'border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-300', pulse: true },
  cancelling: { label: '取消中', className: 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-300', pulse: true },
  completed: { label: '已完成', className: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300' },
  failed: { label: '失败', className: 'border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-300' },
  cancelled: { label: '已取消', className: 'border-slate-400/30 bg-slate-400/10 text-slate-500 dark:text-slate-400' },
  stale: { label: '已过期', className: 'border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-300' },
  // 通用语义（图层 / 数据源同步状态等）
  active: { label: '活跃', className: 'border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-300', pulse: true },
  ok: { label: '正常', className: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300' },
  error: { label: '异常', className: 'border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-300' },
  unknown: { label: '未知', className: 'border-slate-400/30 bg-slate-400/10 text-slate-500 dark:text-slate-400' },
};

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const conf = STATUS_MAP[status] ?? { label: label ?? status, className: STATUS_MAP.unknown.className };
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10px] font-medium',
        conf.className
      )}
    >
      {conf.pulse && (
        <span aria-hidden className="h-1 w-1 animate-pulse rounded-full bg-current motion-reduce:animate-none" />
      )}
      {label ?? conf.label}
    </span>
  );
}

export default StatusBadge;
