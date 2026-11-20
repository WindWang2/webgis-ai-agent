/**
 * refresh-dataset-layer — 数据集编辑提交后的受影响图层增量刷新（G14 抽取）。
 *
 * 从 use-sse-stream 的同款逻辑提取为共享模块（手工编辑桥与 SSE 路径同一
 * 刷新纪律）：按 ref 找 HUD 层、以新 content_revision 走数据面（单飞/ETag；
 * MVT 层走缓存键重建），不 restyle、不动 user-wins 显隐。
 */
import { requestRefFC } from '@/lib/data-plane/ref-service';
import { buildMvtTileUrl } from '@/lib/map-kit/tile-url';
import { useHudStore } from '@/lib/store/useHudStore';

export interface RefreshDatasetLayerOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export function refreshDatasetLayer(
  sid: string,
  refId: string,
  revision: number,
  opts: RefreshDatasetLayerOptions = {},
): void {
  try {
    const hud = useHudStore.getState();
    const layer = (hud.layers as Array<{ id: string; _refId?: string }>).find(
      (l) => l._refId === refId,
    );
    if (!layer) return;
    void requestRefFC({
      sessionId: sid,
      refId,
      dataRevision: revision,
      ownerToken: opts.ownerToken || undefined,
      urgency: 'interactive',
      reasonCode: 'feature-edit:applied',
      signal: opts.signal,
    }).then((res) => {
      if (!res.fc) return;
      const cur = useHudStore.getState();
      const row = (
        cur.layers as unknown as Array<Record<string, unknown> & { id: string; _refId?: string }>
      ).find((l) => l._refId === refId);
      if (!row) return;
      const descriptor = (row._descriptor as Record<string, unknown> | undefined) ?? {};
      const featureCount = descriptor.feature_count as number | undefined;
      const isMvt =
        featureCount !== undefined && featureCount > 5000
        && descriptor.mvt_capable === true
        && typeof (row as { _tileUrl?: unknown })._tileUrl === 'string';
      if (isMvt) {
        // MVT 层不内联 GeoJSON —— descriptor revision 与 _tileUrl（v= 缓存键）
        // 必须一起重建，否则组合 spec 零变化、reconciler 不重加 source。
        cur.updateLayer(row.id, {
          _tileUrl: buildMvtTileUrl(refId, sid, revision),
          _descriptor: { ...descriptor, content_revision: revision } as unknown as never,
        } as never, { source: 'server' });
      } else {
        cur.updateLayer(row.id, {
          source: res.fc as unknown as never,
          _descriptor: { ...descriptor, content_revision: revision } as unknown as never,
        }, { source: 'server' });
      }
    });
  } catch {
    // 刷新失败：SSE mapspec 盖章镜像会随后收敛（content_revision 管线）
  }
}
