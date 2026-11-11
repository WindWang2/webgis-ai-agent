'use client';

/**
 * FeatureEditToolGroup — 真实图层编辑入口（G14 / WP08）。
 *
 * 独立组件（不动全局骨架）：选目标层（带 ref 的 HUD 行）→ 开始/停止编辑
 * （toolSlice.editTarget）；激活后 draw/delete/vertex 工具语义切换到真实
 * 图层事务（FeatureLayerEditor 消费），split 工具在本组内。空 typed 图层
 * 创建走同一入口（provision REST）。
 */
import React, { useCallback, useMemo, useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { createEditDataset } from '@/lib/api/feature-edit';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { refreshDatasetLayer } from '@/lib/edit/refresh-dataset-layer';
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
    <div className="flex items-center gap-1" data-testid="feature-edit-toolbar">
      {editTarget ? (
        <>
          <span className="max-w-24 truncate rounded-xs bg-surface px-1.5 py-0.5 text-micro" data-testid="feature-edit-active-name">
            {(layers ?? []).find((l) => l.id === editTarget.layerId)?.name ?? editTarget.layerId}
          </span>
          <button
            type="button"
            aria-pressed={activeTool === 'split_feature'}
            className={`rounded-xs border border-edge-subtle px-1.5 py-1 hover:bg-surface-hover ${
              activeTool === 'split_feature' ? 'bg-surface-active' : ''
            }`}
            title={t('map.toolbar.splitFeatureTitle')}
            onClick={toggleSplit}
            data-testid="feature-edit-split-tool"
          >
            ✂
          </button>
          <button
            type="button"
            className="rounded-xs border border-edge-subtle px-1.5 py-1 hover:bg-surface-hover"
            title={t('map.edit.stop')}
            onClick={() => setEditTarget(null)}
            data-testid="feature-edit-stop"
          >
            ✕
          </button>
        </>
      ) : (
        <select
          className="max-w-32 rounded-xs border border-edge-subtle bg-surface px-1.5 py-1 text-micro"
          value=""
          data-testid="feature-edit-target-select"
          onChange={(e) => {
            const row = refLayers.find((l) => l.id === e.target.value);
            if (row?._refId) setEditTarget({ layerId: row.id, refId: row._refId });
          }}
          title={t('map.edit.pickTarget')}
        >
          <option value="">{t('map.edit.pickTarget')}</option>
          {refLayers.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>
      )}
      <button
        type="button"
        className="rounded-xs border border-edge-subtle px-1.5 py-1 hover:bg-surface-hover"
        title={t('map.edit.createEmptyTitle')}
        onClick={() => { void createEmpty(); }}
        disabled={busy}
        data-testid="feature-edit-create-empty"
      >
        +
      </button>
      {error && <span className="text-micro text-status-error">{error}</span>}
    </div>
  );
}
