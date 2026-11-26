'use client';

/**
 * FeatureEditToolGroup — 真实图层编辑入口（G14 / WP08）。
 *
 * 独立组件（不动全局骨架）：选目标层（带 ref 的 HUD 行）→ 开始/停止编辑
 * （toolSlice.editTarget）；激活后 draw/delete/vertex 工具语义切换到真实
 * 图层事务（FeatureLayerEditor 消费），split 工具在本组内。空 typed 图层
 * 创建走同一入口（provision REST）。
 *
 * G32：另含「要素变更提案队列」入口（FeatureProposalDrawer）— 即时编辑
 * 保持不变；跨会话待审候选在此排队审阅。
 */
import React, { useCallback, useMemo, useState } from 'react';
import { ClipboardList, Plus, Scissors, X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { createEditDataset } from '@/lib/api/feature-edit';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { refreshDatasetLayer } from '@/lib/edit/refresh-dataset-layer';
import { FeatureProposalDrawer } from '@/components/workbench/feature-proposal-drawer';
import { useT } from '@/lib/i18n/useT';

interface RefLayerRow {
  id: string;
  name: string;
  _refId?: string;
}

export function FeatureEditToolGroup() {
  const t = useT();
  const layers = useHudStore((s) => s.layers) as unknown as RefLayerRow[] | undefined;
  const editTarget = useHudStore((s) => s.editTarget);
  const setEditTarget = useHudStore((s) => s.setEditTarget);
  const activeTool = useHudStore((s) => s.activeMapTool);
  const setActiveMapTool = useHudStore((s) => s.setActiveMapTool);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposalsOpen, setProposalsOpen] = useState(false);

  const refLayers = useMemo(
    () => (layers ?? []).filter((l) => typeof l._refId === 'string' && l._refId),
    [layers],
  );

  const toggleSplit = useCallback(() => {
    if (!editTarget) return;
    setActiveMapTool(activeTool === 'split_feature' ? null : 'split_feature');
  }, [editTarget, activeTool, setActiveMapTool]);

  const createEmpty = useCallback(async () => {
    const { sessionId } = getSessionIdentity();
    if (!sessionId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await createEditDataset(sessionId, {
        name: t('map.edit.newLayerName'),
        geometryType: 'Polygon',
      });
      if (result.ok) {
        // SSE mapspec 镜像稍后同步 HUD 行；主动刷新一次数据面收敛更快
        refreshDatasetLayer(sessionId, result.ref_id, result.content_revision ?? 1);
      } else {
        setError(t('map.edit.createFailed'));
      }
    } catch {
      setError(t('map.edit.networkError'));
    } finally {
      setBusy(false);
    }
  }, [busy, t]);

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1 border-t border-edge-subtle pt-2" data-testid="feature-edit-toolbar">
      {editTarget ? (
        <>
          <span className="min-w-0 flex-1 truncate px-1 text-meta font-medium text-ink" data-testid="feature-edit-active-name">
            {(layers ?? []).find((l) => l.id === editTarget.layerId)?.name ?? editTarget.layerId}
          </span>
          <button
            type="button"
            aria-pressed={activeTool === 'split_feature'}
            className={`inline-flex h-8 w-8 items-center justify-center rounded-sm hover:bg-surface-hover ${
              activeTool === 'split_feature' ? 'bg-surface-selected' : ''
            }`}
            title={t('map.toolbar.splitFeatureTitle')}
            aria-label={t('map.toolbar.splitFeatureTitle')}
            onClick={toggleSplit}
            data-testid="feature-edit-split-tool"
          >
            <Scissors size={16} aria-hidden />
          </button>
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-sm hover:bg-surface-hover"
            title={t('map.edit.stop')}
            aria-label={t('map.edit.stop')}
            onClick={() => setEditTarget(null)}
            data-testid="feature-edit-stop"
          >
            <X size={16} aria-hidden />
          </button>
        </>
      ) : (
        <select
          className="h-8 min-w-0 flex-1 rounded-sm border border-edge-subtle bg-surface-raised px-1.5 text-meta text-ink"
          value=""
          data-testid="feature-edit-target-select"
          onChange={(e) => {
            const row = refLayers.find((l) => l.id === e.target.value);
            if (row?._refId) setEditTarget({ layerId: row.id, refId: row._refId });
          }}
          title={t('map.edit.pickTarget')}
          aria-label={t('map.edit.pickTarget')}
        >
          <option value="">{t('map.edit.pickTarget')}</option>
          {refLayers.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>
      )}
      <button
        type="button"
        className="inline-flex h-8 w-8 items-center justify-center rounded-sm hover:bg-surface-hover"
        title={t('map.edit.createEmptyTitle')}
        aria-label={t('map.edit.createEmptyTitle')}
        onClick={() => { void createEmpty(); }}
        disabled={busy}
        data-testid="feature-edit-create-empty"
      >
        <Plus size={16} aria-hidden />
      </button>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={proposalsOpen}
        className="inline-flex h-8 w-8 items-center justify-center rounded-sm hover:bg-surface-hover"
        title={t('featureProposals.openQueueTitle')}
        aria-label={t('featureProposals.openQueueTitle')}
        onClick={() => setProposalsOpen(true)}
        data-testid="feature-proposal-open-queue"
      >
        <ClipboardList size={14} aria-hidden />
      </button>
      {error && <span role="alert" className="w-full text-meta text-status-critical">{error}</span>}
      <FeatureProposalDrawer open={proposalsOpen} onClose={() => setProposalsOpen(false)} />
    </div>
  );
}
