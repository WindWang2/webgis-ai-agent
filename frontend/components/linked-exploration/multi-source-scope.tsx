/**
 * 多源联动范围组件（WP4）：逐源 scope chips + 传播披露 + 指标组表。
 *
 * 纯展示（props 驱动）——数据由父级经 REST plan-preview 获取（与 Agent
 * webgis_exploration_plan_preview 同一生产服务）。诚实披露纪律：
 * unavailable 组展示原因码（不冒充 0）；键帽/复合键跳过 handle 披露；
 * 未上图/不可投影源按后端口径说明渲染。
 */
'use client';

import { useT } from '@/lib/i18n/useT';
import type {
  MetricResult,
  PlanPreviewResponse,
  SourceScopeEntry,
} from '@/lib/linked-exploration/contracts';

interface Props {
  preview: PlanPreviewResponse | null;
  loading?: boolean;
  error?: { code: string; message: string; hint?: string } | null;
}

function ScopeEntry({ entry }: { entry: SourceScopeEntry }) {
  const t = useT('linkedExploration');
  const handleSkipped = entry.selection_handle_skipped;
  return (
    <li className="ms-source" data-testid={`ms-source-${entry.source_id}`}>
      <span className="ms-source__id" data-testid={`ms-source-role-${entry.source_id}`}>
        {entry.source_id}
        {entry.role === 'primary' ? ' · ' + t('multiSource.primary') : ''}
      </span>
      <span className="ms-source__count" data-testid={`ms-source-count-${entry.source_id}`}>
        {entry.match_count} / {entry.total}
      </span>
      {entry.propagations.map((p) => (
        <span
          key={p.link_id}
          className="ms-source__prop"
          data-testid={`ms-prop-${entry.source_id}-${p.link_id}`}
          title={p.from_unfiltered
            ? t('multiSource.propUnfiltered')
            : `${t('multiSource.propKeys')}: ${p.key_count}`}
        >
          {p.from_unfiltered
            ? t('multiSource.propNone')
            : `→ ${p.key_count} ${t('multiSource.keys')}`}
        </span>
      ))}
      {entry.map_filter_projected === false && (
        <span className="ms-source__unprojected" data-testid={`ms-unprojected-${entry.source_id}`}>
          {t('multiSource.unprojected')}
        </span>
      )}
      {handleSkipped && (
        <span className="ms-source__noscope" data-testid={`ms-noscope-${entry.source_id}`}>
          {t('multiSource.handleSkipped')}
        </span>
      )}
    </li>
  );
}

function MetricBlock({ metric }: { metric: MetricResult }) {
  const t = useT('linkedExploration');
  const unitLabel = metric.unit ? ` (${metric.unit})` : '';
  return (
    <div className="ms-metric" data-testid={`ms-metric-${metric.metric_id}`}>
      <div className="ms-metric__title">
        {metric.title || metric.metric_id} · {metric.op}
        {unitLabel}
        {metric.scale ? ` ×${metric.scale}` : ''}
      </div>
      <ul className="ms-metric__groups">
        {metric.groups.map((g) => (
          <li key={String(g.key)} data-testid={`ms-metric-group-${metric.metric_id}-${g.key}`}>
            <span className="ms-metric__key">{g.key_label}</span>
            {g.available ? (
              <span className="ms-metric__value" data-testid={`ms-metric-value-${metric.metric_id}-${g.key}`} data-available="true">
                {g.value}
              </span>
            ) : (
              <span
                className="ms-metric__value"
                data-testid={`ms-metric-value-${metric.metric_id}-${g.key}`}
                data-available="false"
                data-reason={g.unavailable_reason ?? 'unknown'}
                title={g.unavailable_reason ?? ''}
              >
                {t('multiSource.unavailable')}
                {g.unavailable_reason ? ` (${g.unavailable_reason})` : ''}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MultiSourceScope({ preview, loading, error }: Props) {
  const t = useT('linkedExploration');
  if (error) {
    return (
      <div className="ms-scope ms-scope--error" role="alert" data-testid="ms-error">
        <p>{error.message}</p>
        {error.hint && <p className="ms-scope__hint">{error.hint}</p>}
      </div>
    );
  }
  if (loading) {
    return (
      <div className="ms-scope" role="status" data-testid="ms-loading">
        {t('preview.loading')}
      </div>
    );
  }
  if (!preview) {
    return (
      <div className="ms-scope ms-scope--empty" data-testid="ms-empty">
        <p>{t('multiSource.empty')}</p>
      </div>
    );
  }
  const sourceIds = Object.keys(preview.per_source);
  return (
    <div className="ms-scope" data-testid="ms-scope">
      <ul className="ms-scope__sources" data-testid="ms-sources">
        {sourceIds.map((sid) => (
          <ScopeEntry key={sid} entry={preview.per_source[sid]} />
        ))}
      </ul>
      {preview.metrics.length > 0 && (
        <div className="ms-scope__metrics" data-testid="ms-metrics">
          {preview.metrics.map((m) => (
            <MetricBlock key={m.metric_id} metric={m} />
          ))}
        </div>
      )}
    </div>
  );
}

export default MultiSourceScope;
