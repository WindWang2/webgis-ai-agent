'use client';

import { useState } from 'react';
import { ChevronRight, Database, GitCommitHorizontal, Tag } from 'lucide-react';
import type {
  DatasetRef,
  DatasetVersion,
  LakehouseDataset,
} from '@/lib/api/lakehouse';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { STitle } from '@/components/shared/section-title';
import { useLakehouseDatasetDetail, useLakehouseDatasets } from './use-lakehouse-datasets';

export interface DatasetsPanelProps {
  ownerType: 'session' | 'project';
  sessionId: string;
  ownerToken?: string | null;
  /** 双快照对比入口（P6 diff 视图在本 tab 内渲染）。 */
  onOpenDiff?: (datasetId: string, versions: [string, string]) => void;
}
function RefChips({ refs }: { refs: DatasetRef[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {refs.map((r) => (
        <span
          key={`${r.ref_type}-${r.ref_name}`}
          className="inline-flex items-center gap-1 rounded-pill bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary"
          title={`${r.ref_type} @ ${r.version_id.slice(0, 12)} (gen ${r.generation})`}
        >
          {r.ref_type === 'tag' ? <Tag size={10} aria-hidden /> : <GitBranchIcon aria-hidden />}
          {r.ref_name}
        </span>
      ))}
    </div>
  );
}

function GitBranchIcon() {
  return <GitCommitHorizontal size={10} aria-hidden />;
}

function formatBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(1)} KB`;
  return `${n} B`;
}

/**
 * Dataset 版本层面板（V8）：清单 → 详情（refs/head/descriptor）→ 版本历史。
 * 纯浏览视图；commit/branch/tag/rollback 语义动作归 P6 版本工作流。
 */
export function DatasetsPanel({ ownerType, sessionId, ownerToken }: DatasetsPanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { datasets, loading, error, refresh } = useLakehouseDatasets({ ownerType, sessionId, ownerToken });
  const { detail, versions, loading: detailLoading, error: detailError } = useLakehouseDatasetDetail({
    datasetId: selectedId,
    sessionId,
    ownerToken,
  });

  if (ownerType === 'project') {
    return (
      <EmptyState
        icon={Database}
        title="数据集是会话域资源"
        description="dataset REST 面仅暴露 session 域；项目域请经发布流程（P6）访问。"
      />
    );
  }

  if (!sessionId) {
    return (
      <EmptyState
        icon={Database}
        title="暂无活跃会话"
        description="数据集挂在会话上 —— 打开或创建会话后在此浏览。"
      />
    );
  }

  // 选中态：单数据集详情
  if (selectedId) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto px-panel py-2" data-testid="lakehouse-dataset-detail">
        <button
          type="button"
          onClick={() => setSelectedId(null)}
          className="mb-2 flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-caption text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          <ChevronRight size={12} aria-hidden className="rotate-180" />
          返回清单
        </button>
        {detailLoading && <LoadingState label="正在读取数据集…" />}
        {detailError && <InlineNotice variant="error">{detailError}</InlineNotice>}
        {detail && (
          <div className="space-y-3">
            <STitle title={detail.dataset.name} sub={detail.dataset.description ?? undefined} />
            <div className="space-y-1 rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2 text-caption">
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">默认分支</span>
                <span className="font-mono text-ink">{detail.dataset.default_branch}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">head</span>
                <span className="font-mono text-ink">
                  {detail.head ? detail.head.version_id.slice(0, 12) : '（空分支）'}
                </span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">创建于</span>
                <span className="text-ink">{detail.dataset.created_at?.slice(0, 19).replace('T', ' ') ?? '—'}</span>
              </div>
            </div>

            <STitle title="分支与标签" />
            <RefChips refs={detail.refs} />

            <STitle title={`版本历史（${versions.length}）`} />
            {versions.length === 0 ? (
              <p className="text-caption text-ink-muted">尚无提交。</p>
            ) : (
              <ol className="space-y-1" data-testid="lakehouse-version-list">
                {versions.map((v: DatasetVersion) => (
                  <li
                    key={v.version_id}
                    className="rounded-sm border border-edge-subtle bg-surface-overlay px-2 py-1.5 text-caption"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-micro text-ink">{v.version_id.slice(0, 12)}</span>
                      <span className="rounded-sm bg-surface-sunken px-1 text-micro text-ink-secondary">
                        {v.action}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-micro text-ink-muted">
                      <span>{v.created_at?.slice(0, 19).replace('T', ' ') ?? '—'}</span>
                      <span>{formatBytes(v.byte_size)}</span>
                      {v.parent_version_id && (
                        <span className="truncate">← {v.parent_version_id.slice(0, 8)}</span>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>
    );
  }

  // 清单态
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-panel py-2" data-testid="lakehouse-datasets-list">
      {error && <InlineNotice variant="error">{error}</InlineNotice>}
      {loading && <LoadingState label="正在读取数据集清单…" />}
      {!loading && !error && datasets.length === 0 && (
        <EmptyState
          icon={Database}
          title="会话中还没有数据集"
          description="cube 构建后注册为 dataset（V8 版本层）；或经 agent 工具链创建。"
          action={{ label: '重新加载', onClick: refresh }}
        />
      )}
      <ul className="space-y-1.5">
        {datasets.map((d: LakehouseDataset) => (
          <li key={d.dataset_id}>
            <button
              type="button"
              onClick={() => setSelectedId(d.dataset_id)}
              data-testid={`lakehouse-dataset-${d.name}`}
              className="w-full rounded-md border border-l-2 border-edge-subtle border-l-transparent bg-surface-overlay px-panel py-2 text-left transition-colors hover:bg-surface-hover"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-body font-semibold text-ink">{d.name}</span>
                <ChevronRight size={14} aria-hidden className="shrink-0 text-ink-muted" />
              </div>
              <p className="mt-0.5 line-clamp-1 text-caption text-ink-muted">
                {d.description || d.dataset_id.slice(0, 16)}
              </p>
              {d.cube_contract ? (
                <p className="mt-1 truncate font-mono text-micro text-ink-muted">
                  {JSON.stringify(d.cube_contract).slice(0, 60)}
                </p>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
