'use client';

/**
 * Workers 表（P3）—— 心跳新鲜度分档 / 负载槽位 / 能力披露 / 对象缓存。
 *
 * a11y：原生 <table>，行可聚焦（roving tabindex + 方向键），能力缺席显示
 * 「未披露」而不是 0（旧 worker capability=null 是诚实缺失）。
 */
import { useCallback, useRef } from 'react';
import type { ClusterWorker } from '@/lib/api/geocompute';
import { heartbeatFreshness, type HeartbeatFreshness } from '@/lib/hooks/use-cluster-workers';
import { formatBytes } from './ops-shared';

const FRESHNESS_CONF: Record<HeartbeatFreshness, { label: string; className: string }> = {
  fresh: { label: '新鲜', className: 'text-status-success' },
  warm: { label: '迟滞', className: 'text-status-warning' },
  stale: { label: '失联', className: 'text-status-critical' },
};

function profileSummary(profiles: Record<string, number>): string {
  const entries = Object.entries(profiles);
  if (entries.length === 0) return '—';
  return entries.map(([profile, slots]) => `${profile}×${slots}`).join(' ');
}

export function WorkersTable({ workers }: { workers: ClusterWorker[] }) {
  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (target.tagName !== 'TR') return;
    const rows = rowRefs.current;
    const idx = rows.indexOf(target as HTMLTableRowElement);
    if (idx < 0) return;
    let next = -1;
    if (e.key === 'ArrowDown') next = Math.min(rows.length - 1, idx + 1);
    else if (e.key === 'ArrowUp') next = Math.max(0, idx - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = rows.length - 1;
    if (next < 0) return;
    e.preventDefault();
    rows[next]?.focus();
  }, []);

  if (workers.length === 0) {
    return (
      <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
        无 worker 在线
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table
        className="w-full border-collapse text-left"
        aria-label="集群 worker 列表"
        onKeyDown={onKeyDown}
      >
        <thead>
          <tr className="border-b border-edge-subtle text-micro text-ink-muted">
            <th scope="col" className="px-1.5 py-1 font-medium">Worker</th>
            <th scope="col" className="px-1.5 py-1 font-medium">角色</th>
            <th scope="col" className="px-1.5 py-1 font-medium">心跳</th>
            <th scope="col" className="px-1.5 py-1 font-medium">槽位</th>
            <th scope="col" className="px-1.5 py-1 font-medium">能力</th>
            <th scope="col" className="px-1.5 py-1 font-medium">对象缓存</th>
          </tr>
        </thead>
        <tbody>
          {workers.map((w, i) => {
            const fresh = heartbeatFreshness(w.heartbeat_age_s);
            const conf = FRESHNESS_CONF[fresh];
            return (
              <tr
                key={w.worker_id}
                ref={(el) => {
                  rowRefs.current[i] = el;
                }}
                tabIndex={0}
                aria-rowindex={i + 1}
                data-testid={`worker-row-${w.worker_id}`}
                className="border-b border-edge-subtle/60 text-meta text-ink-secondary outline-none focus-visible:bg-surface-hover"
              >
                <td className="max-w-[9rem] truncate px-1.5 py-1 font-mono text-micro" title={w.worker_id}>
                  {w.worker_id}
                </td>
                <td className="px-1.5 py-1 text-micro">
                  {w.role === 'coordinator' ? '协调器' : '执行器'}
                </td>
                <td className="px-1.5 py-1 text-micro tabular-nums">
                  <span className={`font-medium ${conf.className}`}>{conf.label}</span>{' '}
                  {w.heartbeat_age_s.toFixed(0)}s
                </td>
                <td className="px-1.5 py-1 text-micro" title={profileSummary(w.profiles)}>
                  {profileSummary(w.profiles)}
                </td>
                <td className="px-1.5 py-1 text-micro">
                  {w.capability
                    ? `GPU×${Array.isArray((w.capability as { gpus?: unknown[] }).gpus) ? (w.capability as { gpus: unknown[] }).gpus.length : 0}`
                    : '未披露'}
                </td>
                <td className="px-1.5 py-1 text-micro tabular-nums">
                  {w.cache_entries > 0 ? `${w.cache_entries} 项 / ${formatBytes(w.cache_bytes)}` : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
