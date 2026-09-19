'use client';

/**
 * 系统健康分区（P5，ADR-0142 D6 —— 挂在设置面板「系统 → 集群健康」子 tab，
 * 亦被 ops 控制台复用为「系统」视图）。
 *
 * 数据通道（master 实际形状；无 /healthz）：
 * - GET /api/v1/health + /ready   基础卡（无认证，10s）
 * - GET /api/v1/version           构建/扩展 API 信息（60s）
 * - GET /api/v1/status/detailed   组件级 db/redis/llm/worker/object_store（10s，JWT）
 * - durable 队列深度「双口径」明确标注：
 *     全局 = /geocompute/cluster/metrics queue_depth/inflight（require_admin）
 *     owner 域 = /tasks/jobs?active_only=true 计数
 * - 错误分类 top-N：后端无端点 → 诚实空态卡（#607 原则，协调点）。
 * 通道自检卡展示本分区轮询通道的 lastFetchedAt / 连错 / 暂停态。
 */
import { useMemo } from 'react';
import { HeartPulse, Database, Radio } from 'lucide-react';
import { StatusBadge } from '@/components/shared/status-badge';
import {
  getHealthBasic,
  getReadyProbe,
  getVersionInfo,
  getDetailedStatus,
  type DetailedStatus,
  type HealthBasic,
  type VersionInfo,
  type ComponentHealth,
} from '@/lib/api/system-health';
import { listJobs } from '@/lib/api/jobs';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';
import { useClusterMetrics } from '@/lib/hooks/use-cluster-metrics';
import { ChannelStateBadge, HonestEmptyCard, MetricTile, OpsCard, formatDuration, formatTime, type OpsChannelState } from './ops-shared';
import { useT } from '@/lib/i18n/useT';

const COMPONENT_LABELS: Record<string, string> = {
  db: '数据库',
  redis: 'Redis',
  llm: 'LLM 网关',
  worker: 'Worker',
  object_store: '对象存储',
};

const COMPONENT_STATUS_MAP: Record<ComponentHealth['status'], string> = {
  ok: 'ok',
  degraded: 'warning',
  down: 'error',
  not_configured: 'unknown',
};

export function SystemHealthPanel({
  ownerToken,
}: {
  ownerToken?: string | null;
}) {
  const t = useT('ops');
  const basic = useBoundedPoll<HealthBasic>({
    pollIntervalMs: 10_000,
    fetcher: (signal) => getHealthBasic({ signal }),
  });
  const ready = useBoundedPoll<{ ready: boolean }>({
    pollIntervalMs: 10_000,
    fetcher: (signal) => getReadyProbe({ signal }),
  });
  const version = useBoundedPoll<VersionInfo>({
    pollIntervalMs: 60_000,
    fetcher: (signal) => getVersionInfo({ signal }),
  });
  const detailed = useBoundedPoll<DetailedStatus>({
    pollIntervalMs: 10_000,
    fetcher: (signal) => getDetailedStatus({ signal }),
  });
  const ownerQueue = useBoundedPoll<{ active: number; pollAfterMs: number | null }>({
    pollIntervalMs: 6000,
    fetcher: async (signal) => {
      const res = await listJobs({ ownerToken, signal, activeOnly: true });
      return { active: res.jobs.filter((j) => j.active).length, pollAfterMs: res.poll_after_ms };
    },
  });
  const globalMetrics = useClusterMetrics({ ownerToken, pollIntervalMs: 15_000 });

  const channels = useMemo(
    () =>
      [
        { name: '/health + /ready', channel: basic.data ? 'live' : basic.error ? 'error' : 'loading', status: basic.status },
        { name: '/version', channel: version.data ? 'live' : version.error ? 'error' : 'loading', status: version.status },
        { name: '/status/detailed', channel: detailed.data ? 'live' : detailed.error ? 'error' : 'loading', status: detailed.status },
        { name: '/tasks/jobs（owner 域）', channel: ownerQueue.data ? 'live' : ownerQueue.error ? 'error' : 'loading', status: ownerQueue.status },
        { name: '/cluster/metrics（全局）', channel: globalMetrics.channel, status: globalMetrics.status },
      ] as { name: string; channel: OpsChannelState; status: { lastFetchedAt: string | null; consecutiveErrors: number; paused: boolean } }[],
    [basic.data, basic.error, basic.status, version.data, version.error, version.status, detailed.data, detailed.error, detailed.status, ownerQueue.data, ownerQueue.error, ownerQueue.status, globalMetrics.channel, globalMetrics.status],
  );

  return (
    <div className="flex flex-col gap-3" data-testid="ops-system-health">
      {/* 基础健康 + 就绪 */}
      <OpsCard
        title={t('kg2kway')}
        sub={t('apiV1HealthReady')}
        actions={<ChannelStateBadge channel={basic.data ? 'live' : basic.error ? 'error' : 'loading'} />}
        testId="ops-health-basic"
      >
        {basic.data ? (
          <div className="grid grid-cols-2 gap-1.5">
            <MetricTile label={t('klapj')} value={basic.data.status} tone={basic.data.status === 'healthy' ? 'success' : 'warning'} />
            <MetricTile label={t('kemjzhh')} value={ready.data ? (ready.data.ready ? 'ready' : 'not ready') : '—'} tone={ready.data?.ready ? 'success' : 'critical'} />
            <MetricTile label="agent runtime" value={basic.data.agent_runtime} hint={basic.data.pi_workers_alive ?? undefined} />
            <MetricTile label={t('kg2r2v6')} value={basic.data.version} hint={formatTime(basic.data.timestamp)} />
          </div>
        ) : (
          <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
            {basic.error ? `健康端点不可达：${basic.error}` : '正在探测…'}
          </p>
        )}
      </OpsCard>

      {/* 组件级状态（JWT） */}
      <OpsCard
        title={t('kjo1f01')}
        sub={t('apiV1StatusDetailedjwt10s')}
        actions={<ChannelStateBadge channel={detailed.data ? 'live' : detailed.error ? 'error' : 'loading'} />}
        testId="ops-health-components"
      >
        {detailed.data ? (
          <>
            <ul className="flex flex-col gap-1" aria-label={t('k2mc0vi')}>
              {Object.entries(detailed.data.components).map(([name, comp]) => (
                <li key={name} className="flex items-center justify-between gap-2 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1">
                  <span className="flex items-center gap-1.5 text-micro text-ink-secondary">
                    <Database size={11} aria-hidden />
                    {COMPONENT_LABELS[name] ?? name}
                  </span>
                  <span className="flex min-w-0 items-center gap-1.5 text-micro text-ink-muted">
                    {comp.detail && (
                      <span className="max-w-[7rem] truncate" title={comp.detail}>
                        {comp.detail}
                      </span>
                    )}
                    {comp.latency_ms != null && <span className="tabular-nums">{comp.latency_ms.toFixed(1)}ms</span>}
                    <StatusBadge status={COMPONENT_STATUS_MAP[comp.status] ?? 'unknown'} label={comp.status} />
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-micro text-ink-muted">
              {t('khuy6')}<StatusBadge status={detailed.data.status === 'ok' ? 'ok' : detailed.data.status === 'degraded' ? 'warning' : 'error'} label={detailed.data.status} />
              {detailed.data.stuck_jobs != null && ` · stuck jobs ${detailed.data.stuck_jobs}`}
            </p>
          </>
        ) : (
          <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
            {detailed.error ? `组件状态不可用：${detailed.error}` : '正在探测…'}
          </p>
        )}
      </OpsCard>

      {/* durable 队列深度（双口径） */}
      <OpsCard
        title={t('durable')}
        sub={t('adminOwner')}
        actions={<ChannelStateBadge channel={globalMetrics.channel === 'live' || globalMetrics.channel === 'admin-required' ? globalMetrics.channel : ownerQueue.data ? 'live' : 'loading'} />}
        testId="ops-queue-depth"
      >
        <div className="grid grid-cols-2 gap-1.5">
          <MetricTile
            label={t('owner3')}
            value={ownerQueue.data ? String(ownerQueue.data.active) : '—'}
            hint={t('tasksJobsActiveOnly')}
            tone="info"
          />
          <MetricTile
            label={t('queueInflight')}
            value={globalMetrics.data ? `${globalMetrics.data.queue_depth} / ${globalMetrics.data.inflight}` : '—'}
            hint={globalMetrics.channel === 'admin-required' ? '需管理员权限' : 'cluster/metrics'}
          />
        </div>
        <p className="text-micro text-ink-muted">
          {t('ownerRequireAdmin')}</p>
      </OpsCard>

      {/* 版本 / 构建信息 */}
      <OpsCard
        title={t('kg76966')}
        sub="/api/v1/version"
        actions={<span className="flex items-center gap-1 text-micro text-ink-muted"><HeartPulse size={11} aria-hidden /></span>}
        testId="ops-version"
      >
        {version.data ? (
          <div className="grid grid-cols-2 gap-1.5">
            <MetricTile label="version" value={version.data.version} />
            <MetricTile label="commit" value={version.data.commit.slice(0, 12)} />
            <MetricTile label="python" value={version.data.python} />
            <MetricTile label="extensions_api" value={version.data.extensions_api} />
          </div>
        ) : (
          <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
            {version.error ? '版本端点不可达' : '正在获取…'}
          </p>
        )}
        <p className="text-micro text-ink-muted">{t('flags')}</p>
      </OpsCard>

      {/* 错误分类 top-N（#607 诚实空态 + 协调点） */}
      <OpsCard title={t('topN')} sub={t('kig3lkp')} testId="ops-error-taxonomy">
        <HonestEmptyCard
          title={t('k1mj04lm')}
          reason={t('failureTaxonomyRemediationledgerHttpP0')}
        />
      </OpsCard>

      {/* 通道自检 */}
      <OpsCard
        title={t('k1wjfm45')}
        sub={t('sseSse')}
        actions={<span className="flex items-center gap-1 text-micro text-ink-muted"><Radio size={11} aria-hidden /></span>}
        testId="ops-channel-check"
      >
        <ul className="flex flex-col gap-1" aria-label={t('k1j86mwk')}>
          {channels.map((c) => (
            <li key={c.name} className="flex items-center justify-between gap-2 text-micro text-ink-secondary">
              <span className="truncate font-mono">{c.name}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                <span className="text-ink-muted">
                  {c.status.lastFetchedAt ? `${formatDuration((Date.now() - Date.parse(c.status.lastFetchedAt)) / 1000)}前` : '—'}
                </span>
                {c.status.consecutiveErrors > 0 && (
                  <span className="text-status-critical">{t('knzdskv', { p0: c.status.consecutiveErrors })}</span>
                )}
                <ChannelStateBadge channel={c.channel} />
              </span>
            </li>
          ))}
        </ul>
      </OpsCard>
    </div>
  );
}
