'use client';

/**
 * ops 控制台本地原语（ADR-0142 D1）—— 卡片/指标砖/通道徽标/权限诚实态。
 *
 * 通道态与权限态是本控制台的一等公民（D7）：require_admin 的 403 与
 * 控制面 503 不是「错误横幅」而是专属 UI 态；所有面板共享同一词表。
 */
import type { ReactNode } from 'react';
import { ShieldAlert, ServerOff } from 'lucide-react';
import { StatusBadge, type StatusTone } from '@/components/shared/status-badge';
import { InlineNotice } from '@/components/shared/inline-notice';

/* ------------------------------------------------------------------ */
/* 展示格式化                                                          */
/* ------------------------------------------------------------------ */

export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m${s > 0 ? `${s}s` : ''}`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60 > 0 ? `${m % 60}m` : ''}`;
}

export function formatPercent(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio)) return '—';
  return `${Math.round(ratio * 100)}%`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

/* ------------------------------------------------------------------ */
/* 卡片与指标砖                                                        */
/* ------------------------------------------------------------------ */

export function OpsCard({
  title,
  sub,
  actions,
  children,
  testId,
}: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className="flex flex-col gap-2 rounded-md border border-edge-subtle bg-surface-raised p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-body font-semibold text-ink">{title}</h3>
          {sub && <p className="text-micro text-ink-muted">{sub}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function MetricTile({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'neutral' | 'info' | 'success' | 'warning' | 'critical';
}) {
  const toneClass: Record<string, string> = {
    neutral: 'text-ink',
    info: 'text-status-info',
    success: 'text-status-success',
    warning: 'text-status-warning',
    critical: 'text-status-critical',
  };
  return (
    <div className="flex min-w-0 flex-col rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1.5">
      <span className="truncate text-micro text-ink-muted" title={label}>
        {label}
      </span>
      <span className={`text-heading font-semibold tabular-nums ${toneClass[tone]}`} data-testid={`tile-${label}`}>
        {value}
      </span>
      {hint && <span className="truncate text-micro text-ink-muted">{hint}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 通道态徽标（轮询通道自检的统一词表）                                  */
/* ------------------------------------------------------------------ */

export type OpsChannelState =
  | 'idle'
  | 'loading'
  | 'live'
  | 'admin-required'
  | 'unavailable'
  | 'error'
  | 'paused'
  | 'terminal'
  | 'notfound';

const CHANNEL_CONF: Record<OpsChannelState, { label: string; status: string }> = {
  idle: { label: '未启用', status: 'unknown' },
  loading: { label: '加载中', status: 'loading' },
  live: { label: '实时', status: 'ok' },
  'admin-required': { label: '需要管理员权限', status: 'warning' },
  unavailable: { label: '集群不可用', status: 'error' },
  error: { label: '错误', status: 'error' },
  paused: { label: '已暂停', status: 'pending' },
  terminal: { label: '已终态', status: 'completed' },
  notfound: { label: '事件不可用', status: 'warning' },
};

export function ChannelStateBadge({ channel }: { channel: OpsChannelState }) {
  const conf = CHANNEL_CONF[channel];
  return <StatusBadge status={conf.status} label={conf.label} />;
}

export function toneForChannel(channel: OpsChannelState): StatusTone {
  const map: Record<OpsChannelState, StatusTone> = {
    idle: 'neutral',
    loading: 'info',
    live: 'success',
    'admin-required': 'warning',
    unavailable: 'critical',
    error: 'critical',
    paused: 'neutral',
    terminal: 'success',
    notfound: 'warning',
  };
  return map[channel];
}

/* ------------------------------------------------------------------ */
/* 权限 / 降级诚实态（D7 —— 永不死按钮）                                */
/* ------------------------------------------------------------------ */

export function PermissionNotice({ description }: { description?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-6 text-center" role="status">
      <ShieldAlert size={18} className="text-status-warning" aria-hidden />
      <p className="text-body font-medium text-ink-secondary">需要管理员权限</p>
      <p className="text-meta text-ink-muted">
        {description ?? '该视图依赖 require_admin 的集群控制面端点，当前账号无权限。'}
      </p>
    </div>
  );
}

export function UnavailableNotice({ description }: { description?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-6 text-center" role="status">
      <ServerOff size={18} className="text-status-critical" aria-hidden />
      <p className="text-body font-medium text-ink-secondary">集群控制面不可用</p>
      <p className="text-meta text-ink-muted">
        {description ?? '后端返回 503（CLUSTER_UNAVAILABLE）。恢复后自动继续轮询。'}
      </p>
    </div>
  );
}

/** 诚实空态卡（#607 原则：能力未接 ≠ 假数据）。 */
export function HonestEmptyCard({ title, reason }: { title: string; reason: string }) {
  return (
    <div className="rounded-sm border border-dashed border-edge-subtle bg-surface-sunken px-3 py-3">
      <p className="text-meta font-medium text-ink-secondary">{title}</p>
      <p className="mt-1 text-micro text-ink-muted">{reason}</p>
    </div>
  );
}

export function OpsInlineError({ message }: { message: string }) {
  return <InlineNotice variant="error">{message}</InlineNotice>;
}
