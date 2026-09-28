'use client';

/**
 * RunTimeline — V7 执行事件时间线（Phase G）。
 *
 * 消费 GET /geocompute/runs/{id}/events 的有界 trace（单页 200 条 +
 * after_id 续读），渲染为纵向时间线：事件词表分组着色（run 终态 / 节点
 * 生命周期 / 治理）。404 = run 已被 retention 清理 —— 显式披露，不装作
 * 「无事件」；503 = 控制面暂不可用。不轮询（任务中心的 3s 轮询驱动展开
 * 时机；手动刷新按钮补一手）。
 */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import {
  getRunEvents,
  RunEventsUnavailableError,
  type GeoComputeRunEvent,
} from '@/lib/api/geocompute';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { useT } from '@/lib/i18n/useT';

const EVENT_TONE: Record<string, 'ok' | 'bad' | 'warn' | 'info'> = {
  run_started: 'info',
  run_completed: 'ok',
  run_failed: 'bad',
  run_cancelled: 'warn',
  run_preempted: 'warn',
  node_failed: 'bad',
  node_cancelled: 'warn',
  node_lost: 'bad',
  node_completed: 'ok',
  node_reused: 'ok',
  node_output_ready: 'ok',
  node_dispatched: 'info',
  node_started: 'info',
  waiting_resource: 'warn',
  worker_cache_hit: 'info',
  straggler_detected: 'warn',
};

const TONE_CLASS: Record<string, string> = {
  ok: 'bg-status-success-soft text-status-success',
  bad: 'bg-status-critical-soft text-status-critical',
  warn: 'bg-status-warn-soft text-status-warn',
  info: 'bg-surface-subtle text-ink-muted',
};

function eventTime(e: GeoComputeRunEvent): string {
  const t = new Date(e.created_at);
  return Number.isNaN(t.getTime()) ? '—' : t.toLocaleTimeString('zh-CN');
}

export function RunTimeline({ runId }: { runId: string }) {
  const t = useT('agent');
  const [events, setEvents] = useState<GeoComputeRunEvent[] | null>(null);
  const [error, setError] = useState<'not_found' | 'unavailable' | 'network' | null>(null);
  const [loading, setLoading] = useState(false);
  const [afterId, setAfterId] = useState(0);
  /** 后端已无可读事件（page.count === 0）—— 续读按钮收起。 */
  const [exhausted, setExhausted] = useState(false);

  const load = useCallback(async (cursor: number, append: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const page = await getRunEvents(runId, { afterId: cursor, limit: 200 });
      setEvents((prev) => (append && prev ? [...prev, ...page.events] : page.events));
      setAfterId(page.after_id);
      // after_id 是升序游标 —— 追加的是**更晚**事件；count 为 0 即耗尽。
      setExhausted(page.count === 0);
    } catch (err) {
      if (err instanceof RunEventsUnavailableError) setError(err.reason);
      else setError('network');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    setEvents(null);
    setAfterId(0);
    setExhausted(false);
    void load(0, false);
  }, [load]);

  const hasMore = events != null && events.length > 0 && afterId > 0
    && events[events.length - 1].id === afterId;

  if (error === 'not_found') {
    return (
      <InlineNotice variant="info">
        {t('timeline.unavailable')}
      </InlineNotice>
    );
  }
  if (error === 'unavailable' || error === 'network') {
    return (
      <InlineNotice variant="error">
        {error === 'unavailable' ? t('timeline.controlPlaneUnavailable') : t('timeline.loadFailed')}
      </InlineNotice>
    );
  }
  if (loading && !events) return <LoadingState label={t('timeline.loading')} />;
  if (!events || events.length === 0) {
    return <p className="px-2 py-1 text-micro text-ink-muted">{t('timeline.empty')}</p>;
  }

  return (
    <div className="space-y-1 py-1" data-testid="run-timeline">
      <div className="flex items-center gap-1 px-2">
        <span className="eyebrow">{t('timeline.heading', { count: events.length })}</span>
        <button
          type="button"
          aria-label={t('timeline.refresh')}
          className="ml-auto rounded-xs p-0.5 text-ink-muted hover:bg-surface-hover hover:text-ink"
          onClick={() => void load(0, false)}
        >
          <RefreshCw aria-hidden size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      <ol className="m-0 list-none space-y-0.5 p-0">
        {events.map((e) => (
          <li key={e.id} className="flex items-start gap-1.5 px-2 py-0.5" data-testid={`run-event-${e.id}`}>
            <span className="shrink-0 font-mono text-micro text-ink-disabled">{eventTime(e)}</span>
            <span className={`shrink-0 rounded-xs px-1 text-micro ${TONE_CLASS[EVENT_TONE[e.event] ?? 'info']}`}>
              {e.event}
            </span>
            {e.node_id && (
              <span className="min-w-0 flex-1 truncate font-mono text-micro text-ink-secondary" title={e.node_id}>
                {e.node_id}
              </span>
            )}
            {typeof e.rows === 'number' && (
                <span className="shrink-0 tabular-nums text-micro text-ink-muted">{t('timeline.rowsCount', { count: e.rows })}</span>
            )}
            {e.error_code && (
              <span className="shrink-0 rounded-xs bg-status-critical-soft px-1 text-micro text-status-critical">
                {e.error_code}
              </span>
            )}
          </li>
        ))}
      </ol>
      {hasMore && !exhausted && (
        <button
          type="button"
          className="mx-2 rounded-xs border border-edge-subtle px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-hover"
          onClick={() => void load(afterId, true)}
        >
          {t('timeline.loadMore')}
        </button>
      )}
    </div>
  );
}

export default RunTimeline;
