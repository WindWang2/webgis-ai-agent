'use client';

import { GitBranch } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import type { LineageGraph } from '@/lib/api/project';
import { buildLineageRows, formatCrs, lineageTruncated, shortId } from '@/lib/workflow/recovery';

export interface LineageListProps {
  artifactId: string;
  artifactCrs?: string | null;
  state: LineageGraph | 'loading' | 'empty' | 'error' | undefined;
  onLoad: (artifactId: string) => void;
}

export function LineageList({ artifactId, artifactCrs, state, onLoad }: LineageListProps) {
  if (!state) {
    return (
      <button
        type="button"
        onClick={() => onLoad(artifactId)}
        className="rounded px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-hover)]"
      >
        加载血统
      </button>
    );
  }
  if (state === 'loading') return <LoadingState label="加载血统…" />;
  if (state === 'error') return <InlineNotice variant="error">血统加载失败</InlineNotice>;
  if (state === 'empty') {
    return <EmptyState icon={GitBranch} title="无血统" description="后端未返回上游或下游边" />;
  }

  const rows = buildLineageRows(state);
  if (rows.length === 0) {
    return <EmptyState icon={GitBranch} title="无血统" description="后端未返回上游或下游边" />;
  }

  return (
    <ul className="space-y-1" aria-label="产物血统">
      <li className="text-[10px] text-[var(--theme-text-muted)]">
        当前产物 CRS {formatCrs(artifactCrs)}
      </li>
      {lineageTruncated(state) && (
        <li className="text-[10px] text-[var(--theme-text-muted)]">仅显示前 {rows.length} 条血统边</li>
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
              数据集 {shortId(row.sourceDatasetId, 8)} · {shortId(row.sourceDatasetFingerprint, 10)}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default LineageList;
