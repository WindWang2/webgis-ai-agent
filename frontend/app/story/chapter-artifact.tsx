'use client';

import React, { useEffect, useState } from 'react';
import { loadChartArtifact } from '@/lib/map-components/chart-artifact';
import { loadTableArtifact } from '@/lib/map-components/table-data';
import { adaptChartData } from '@/lib/chart-adapter';
import type { ChartData } from '@/lib/types';
import { ChartCore } from '@/components/chat/chart-core';
import { TabularDataGrid } from '@/components/explorer/tabular-data-grid';

/**
 * 章节产物回放（ADR-0147）：一个 `ref:*` → 图表或属性表。
 *
 * 数据通道复用 mapspec 图表面板的同一 ref 管线（loadChartArtifact /
 * loadTableArtifact，缓存 + in-flight 去重 + 会话 cursor 鉴权）。加载失败
 * 渲染降级卡片，绝不让单个产物拖垮章节（与 chart-artifact 降级约定一致）。
 */

type TablePayload = Awaited<ReturnType<typeof loadTableArtifact>>;

export function ChapterArtifact({ ref: artifactRef }: { ref: string }): React.ReactElement {
  const isChart = artifactRef.startsWith('ref:chart-');
  const [chart, setChart] = useState<ChartData | null | undefined>(undefined);
  const [table, setTable] = useState<TablePayload | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    if (isChart) {
      void loadChartArtifact(artifactRef).then((data) => {
        if (alive) setChart(data);
      });
    } else {
      void loadTableArtifact(artifactRef).then((data) => {
        if (alive) setTable(data);
      });
    }
    return () => {
      alive = false;
    };
  }, [artifactRef, isChart]);

  const loading = (isChart && chart === undefined) || (!isChart && table === undefined);

  if (loading) {
    return (
      <div className="my-2 rounded-md border border-dashed border-edge-subtle p-3 text-caption text-ink-muted">
        产物加载中…（{artifactRef}）
      </div>
    );
  }

  if (isChart) {
    if (!chart) {
      return <ArtifactUnavailable refId={artifactRef} kind="图表" />;
    }
    return (
      <figure className="my-2 overflow-hidden rounded-md border border-edge-subtle bg-surface-panel" data-testid="chapter-artifact-chart">
        <ChartCore chart={chart} height={220} />
      </figure>
    );
  }

  if (!table || !Array.isArray((table as { columns?: unknown }).columns)) {
    return <ArtifactUnavailable refId={artifactRef} kind="属性表" />;
  }
  const model = table as { columns: Array<{ name: string }>; rows: Record<string, unknown>[] };
  return (
    <div className="my-2 overflow-hidden rounded-md border border-edge-subtle" data-testid="chapter-artifact-table">
      <TabularDataGrid
        columns={model.columns.map((c) => ({
          key: c.name,
          label: c.name,
          type: 'string' as const,
          sortable: true,
        }))}
        data={model.rows ?? []}
        totalCount={model.rows?.length ?? 0}
        defaultPageSize={5}
        enableSort
      />
    </div>
  );
}

function ArtifactUnavailable({ refId, kind }: { refId: string; kind: string }): React.ReactElement {
  return (
    <div role="status" className="my-2 rounded-md border border-edge-subtle bg-surface-sunken p-3 text-caption text-ink-muted">
      {kind}产物不可用（{refId}）。该产物可能已随会话过期，或当前账号无访问权限。
    </div>
  );
}

/** adaptChartData 契约与 chat inline 路径一致 —— 供 ChapterArtifact 边界复用。 */
export function normalizeChapterChart(raw: unknown): ChartData | null {
  return adaptChartData(raw);
}
