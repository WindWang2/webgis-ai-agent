'use client';

/**
 * 断路器与结果缓存面板（P6，只读，ADR-0142 D5 —— 披露留存型）。
 *
 * 事实源：engine_breaker/result_cache 披露内嵌在 data-fabric 联邦查询响应
 * 载荷，无独立轮询端点。本面板展示「最近一次披露」快照（客户端接收时间
 * 明确标注）；从未收到 → 诚实空态。三态（closed/open/half_open）视觉由
 * fixtures 驱动呈现（视觉快照取证）。
 * 协调点：请后端补独立只读端点，落地后本面板切换轮询通道。
 */
import { useEffect, useState } from 'react';
import { Zap, HardDrive } from 'lucide-react';
import { StatusBadge } from '@/components/shared/status-badge';
import {
  getDisclosureSnapshot,
  subscribeFabricDisclosure,
  type BreakerDisclosure,
  type BreakerState,
  type FabricDisclosureSnapshot,
} from '@/lib/api/data-fabric-disclosure';
import { OpsCard, formatBytes, formatDuration, HonestEmptyCard } from './ops-shared';
import { useT } from '@/lib/i18n/useT';

const BREAKER_CONF: Record<BreakerState, { labelKey: string; status: string; ring: string }> = {
  closed: { labelKey: 'kfh4bk1', status: 'ok', ring: 'border-status-success-border bg-status-success-soft text-status-success' },
  open: { labelKey: 'kvhc0qn', status: 'error', ring: 'border-status-critical-border bg-status-critical-soft text-status-critical' },
  half_open: { labelKey: 'kmusb1l', status: 'warning', ring: 'border-status-warning-border bg-status-warning-soft text-status-warning' },
};

/** 冷却剩余：observed_at + cool_down_s 相对当前钟；负数=冷却已过。 */
function coolDownRemaining(d: BreakerDisclosure, nowMs: number): number | null {
  if (d.state !== 'open') return null;
  const observed = Date.parse(d.observed_at);
  if (!Number.isFinite(observed)) return null;
  return Math.max(0, d.cool_down_s - (nowMs - observed) / 1000);
}

export function BreakerPanel() {
  const t = useT('ops');
  const [snap, setSnap] = useState<FabricDisclosureSnapshot>(getDisclosureSnapshot);
  useEffect(() => subscribeFabricDisclosure(setSnap), []);

  const breaker = snap.breaker;
  const cache = snap.cache;
  const nowMs = Date.now();

  return (
    <div className="flex flex-col gap-3" data-testid="ops-breaker-panel">
      <OpsCard
        title={t('fabric')}
        sub={t('engineBreaker')}
        actions={
          breaker && <StatusBadge status={BREAKER_CONF[breaker.state].status} label={BREAKER_CONF[breaker.state].label} />
        }
        testId="ops-breaker-state"
      >
        {breaker ? (
          <>
            <div className="flex items-center gap-3" role="img" aria-label={`断路器状态 ${BREAKER_CONF[breaker.state].label}`}>
              <span
                aria-hidden
                className={`flex h-12 w-12 items-center justify-center rounded-pill border-2 ${BREAKER_CONF[breaker.state].ring}`}
              >
                <Zap size={18} />
              </span>
              <div className="grid flex-1 grid-cols-2 gap-1.5">
                <OpsMiniStat label={t('kmhjtmp')} value={`${breaker.consecutive_failures}/${breaker.failure_threshold}`} />
                <OpsMiniStat label={t('kjpb70u')} value={String(breaker.total_fallbacks)} />
                <OpsMiniStat
                  label={t('kcsokl3')}
                  value={
                    breaker.state === 'open'
                      ? formatDuration(coolDownRemaining(breaker, nowMs))
                      : breaker.state === 'half_open'
                        ? '试验中'
                        : '—'
                  }
                />
                <OpsMiniStat label={t('kfq1wc8')} value={formatTimeSafe(breaker.observed_at)} />
              </div>
            </div>
            <p className="text-micro text-ink-muted">
              {t('dataFabricV6V5')}</p>
          </>
        ) : (
          <HonestEmptyCard
            title={t('k6x15yz')}
            reason={t('federation')}
          />
        )}
      </OpsCard>

      <OpsCard
        title={t('kv5w46c')}
        sub={t('resultCacheBasisTtlFingerprint')}
        actions={<span className="flex items-center gap-1 text-micro text-ink-muted"><HardDrive size={11} aria-hidden /></span>}
        testId="ops-cache-disclosure"
      >
        {cache ? (
          <div className="grid grid-cols-2 gap-1.5">
            <OpsMiniStat label={t('kgch5cr')} value={cache.hit ? 'hit' : 'miss'} />
            <OpsMiniStat label={t('basis')} value={cache.basis} />
            <OpsMiniStat label={t('kgahep3')} value={cache.age_s != null ? formatDuration(cache.age_s) : '分布式（无本地年龄）'} />
            <OpsMiniStat label="TTL" value={cache.ttl_s != null ? formatDuration(cache.ttl_s) : '—'} />
            <OpsMiniStat label={t('key')} value={cache.key ? `${cache.key.slice(0, 8)}…` : '—'} />
            <OpsMiniStat label={t('kfq1wc82')} value={formatTimeSafe(cache.observed_at)} />
          </div>
        ) : (
          <HonestEmptyCard
            title={t('ka1bx4i')}
            reason={t('resultCache')}
          />
        )}
      </OpsCard>

      <p className="text-micro text-ink-muted">
        {t('p0LruStatsHttpP1', { p0: snap.recorded, p1: snap.breaker && ` 最近披露负载 ${formatBytes(JSON.stringify(snap.breaker).length)} 级别。` })}</p>
    </div>
  );
}

function OpsMiniStat({ label, value }: { labelKey: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1">
      <span className="truncate text-micro text-ink-muted">{label}</span>
      <span className="truncate text-meta font-semibold tabular-nums text-ink" title={value}>
        {value}
      </span>
    </div>
  );
}

function formatTimeSafe(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('zh-CN', { hour12: false });
}
