'use client';

import { GitBranch } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import type { LineageGraph } from '@/lib/api/project';
import { buildLineageRows, formatCrs, lineageTruncated, shortId } from '@/lib/workflow/recovery';
import { useT } from '@/lib/i18n/useT';

export interface LineageListProps {
  artifactId: string;
  artifactCrs?: string | null;
  state: LineageGraph | 'loading' | 'empty' | 'error' | undefined;
  onLoad: (artifactId: string) => void;
}

export function LineageList({ artifactId, artifactCrs, state, onLoad }: LineageListProps) {
const t = useT();
  if (!state) {
    return (
      <button
        type="button"
        onClick={() => onLoad(artifactId)}
        className="rounded px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-hover)]"
      >
        {t('sidebar.wf.loadLineage')}
      </button>
    );
  }
  if (state === 'loading') return <LoadingState label={t('sidebar.wf.loadingLineage')} />;
  if (state === 'error') return <InlineNotice variant="error">{t('sidebar.wf.lineageFailed')}</InlineNotice>;
  if (state === 'empty') {
    return <EmptyState icon={GitBranch} title={t('sidebar.wf.noLineage')} description={t('sidebar.wf.noLineageDesc')} />;
  }

  const rows = buildLineageRows(state);
  if (rows.length === 0) {
    return <EmptyState icon={GitBranch} title={t('sidebar.wf.noLineage')} description={t('sidebar.wf.noLineageDesc')} />;
  }

  return (
    <ul className="space-y-1" aria-label={t('sidebar.wf.artifactLineageAria')}>
      <li className="text-[10px] text-[var(--theme-text-muted)]">
        {t('sidebar.wf.currentCrs')} {formatCrs(artifactCrs)}
      </li>
      {lineageTruncated(state) && (
        <li className="text-[10px] text-[var(--theme-text-muted)]">{t('sidebar.wf.limited', { count: rows.length })}</li>
      )}
      {rows.map((row) => (
        <li
          key={row.key}
          className="rounded border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-2 py-1.5"
          style={{ marginLeft: Math.min(row.depth, 4) * 8 }}
        >
          <div className="text-[11px] font-medium text-[var(--theme-text-primary)]">
            {row.direction === 'upstream' ? '上游' : '下游'} · {row.tool}
          </div>
          <div className="font-mono text-[10px] text-[var(--theme-text-muted)]">
            {shortId(row.nodeId, 12)}
            {row.toolVersion ? ` · ${row.toolVersion}` : ''}
          </div>
          {row.sourceDatasetFingerprint && (
            <div className="text-[10px] text-[var(--theme-text-muted)]">
              {t('sidebar.ds.dataset')} {shortId(row.sourceDatasetId, 8)} · {shortId(row.sourceDatasetFingerprint, 10)}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default LineageList;
