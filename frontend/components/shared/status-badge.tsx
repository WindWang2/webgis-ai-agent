'use client';

/**
 * StatusBadge — 统一状态徽标（UI V3 shared primitive）。
 *
 * 覆盖任务中心 8 种 JobStatus（ADR-0052）与通用 active/done/error 语义；
 * dark-safe；running/cancelling 带克制的 pulse 点（reduced-motion 下自动关闭）。
 *
 * UI V4 —— 这里是整个状态色词汇的唯一出处。审计发现三处矛盾：
 *   · `active` 在本文件是 sky，在 source-item-card 却映射到 emerald；
 *   · 「进行中」在 job 语境是蓝色，在 top-bar 的 agent 语境却是绿色；
 *   · 「成功绿」同时存在 #16a34a / emerald-600 / teal-500 三个值。
 * 现在四个语义槽（neutral / info / success / warning / critical）各自只有一个
 * token 三元组，且 in-progress 一律是 info（蓝），成功一律是 success（绿）。
 */
import clsx from 'clsx';

export interface StatusBadgeProps {
  status: string;
  /** 覆盖默认中文标签 */
  label?: string;
}

/** 语义槽 → token 类名。徽标是唯一允许成对使用 soft/border 的地方。 */
export const STATUS_TONE = {
  neutral: 'border-status-neutral-border bg-status-neutral-soft text-status-neutral',
  info: 'border-status-info-border bg-status-info-soft text-status-info',
  success: 'border-status-success-border bg-status-success-soft text-status-success',
  warning: 'border-status-warning-border bg-status-warning-soft text-status-warning',
  critical: 'border-status-critical-border bg-status-critical-soft text-status-critical',
} as const;

export type StatusTone = keyof typeof STATUS_TONE;

const STATUS_MAP: Record<string, { label: string; tone: StatusTone; pulse?: boolean }> = {
  pending: { label: '等待中', tone: 'neutral' },
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info', pulse: true },
  cancelling: { label: '取消中', tone: 'warning', pulse: true },
  completed: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'critical' },
  cancelled: { label: '已取消', tone: 'neutral' },
  stale: { label: '已过期', tone: 'warning' },
  // 结果工作台：分析结果带着告警完成时，语义是 warning 而不是中性灰——
  // 否则「部分完成 / 含告警」与「未知」不可区分（V4 审计 P0）。
  partial: { label: '部分完成', tone: 'warning' },
  warning: { label: '含告警', tone: 'warning' },
  // 通用语义（图层 / 数据源同步状态等）
  active: { label: '活跃', tone: 'info', pulse: true },
  ok: { label: '正常', tone: 'success' },
  error: { label: '异常', tone: 'critical' },
  unknown: { label: '未知', tone: 'neutral' },
  // Layer Manager V2 的封闭状态词表（lib/layers/layer-status；派生自
  // MapSpec + artifact state + RenderObservation，非并行真相）。
  ready: { label: '就绪', tone: 'success' },
  loading: { label: '加载中', tone: 'info', pulse: true },
  rendering: { label: '渲染中', tone: 'info', pulse: true },
  hidden: { label: '已隐藏', tone: 'neutral' },
  expired: { label: '已过期', tone: 'warning' },
};

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const conf = STATUS_MAP[status] ?? { label: label ?? status, tone: 'neutral' as StatusTone };
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-pill border px-1.5 py-0.5 text-micro font-medium',
        STATUS_TONE[conf.tone]
      )}
    >
      {conf.pulse && (
        <span aria-hidden className="h-1 w-1 animate-pulse rounded-pill bg-current motion-reduce:animate-none" />
      )}
      {label ?? conf.label}
    </span>
  );
}

export default StatusBadge;
