/**
 * refresh-dataset-layer 回归：
 * - 同一 ref 挂多个 HUD 层时，编辑后每一行都要刷新 source / _descriptor /
 *   _tileUrl（不能只刷首行、留下旧几何与旧缓存键）；
 * - 取数拒绝或回调写回抛错不得产生 unhandled rejection（失败由 SSE 收敛）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const requestRefFCMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/data-plane/ref-service', () => ({ requestRefFC: requestRefFCMock }));

import { refreshDatasetLayer } from '@/lib/edit/refresh-dataset-layer';
import { useHudStore } from '@/lib/store/useHudStore';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116, 39] },
      properties: { id: 1 },
    },
  ],
};

function layer(id: string, extra: Record<string, unknown>) {
  return {
    id,
    name: id,
    type: 'vector',
    visible: true,
    opacity: 1,
    _refId: 'ref:dup',
    _descriptor: { feature_count: 10, content_revision: 1, mvt_capable: false },
    source: 'old-geometry',
    ...extra,
  };
}

async function captureUnhandled(run: () => void): Promise<unknown[]> {
  const unhandled: unknown[] = [];
  const handler = (reason: unknown) => {
    unhandled.push(reason);
  };
  process.on('unhandledRejection', handler);
  try {
    run();
    await new Promise((resolve) => setTimeout(resolve, 10));
  } finally {
    process.off('unhandledRejection', handler);
  }
  return unhandled;
}

beforeEach(() => {
  requestRefFCMock.mockReset();
  useHudStore.setState({ layers: [] });
});

describe('refreshDatasetLayer', () => {
  it('同一 ref 多图层：每一行都刷新 source 与 descriptor revision', async () => {
    requestRefFCMock.mockResolvedValue({ fc: FC });
    useHudStore.setState({ layers: [layer('L1', {}), layer('L2', {})] as never });

    refreshDatasetLayer('sess-1', 'ref:dup', 7);
    await flush();

    const layers = useHudStore.getState().layers;
    expect(layers).toHaveLength(2);
    for (const l of layers) {
      expect(l.source).toEqual(FC);
      expect((l._descriptor as { content_revision?: number }).content_revision).toBe(7);
    }
  });

  it('同一 ref 多 MVT 图层：每一行都重建 _tileUrl 缓存键与 revision', async () => {
    requestRefFCMock.mockResolvedValue({ fc: FC });
    const descriptor = { feature_count: 6000, content_revision: 1, mvt_capable: true };
    useHudStore.setState({
      layers: [
        layer('M1', { _descriptor: descriptor, _tileUrl: 'tiles?ref=dup&v=1' }),
        layer('M2', { _descriptor: descriptor, _tileUrl: 'tiles?ref=dup&v=1' }),
      ] as never,
    });

    refreshDatasetLayer('sess-1', 'ref:dup', 9);
    await flush();

    const layers = useHudStore.getState().layers;
    expect(layers).toHaveLength(2);
    for (const l of layers) {
      expect(l._tileUrl).toContain('v=9');
      expect((l._descriptor as { content_revision?: number }).content_revision).toBe(9);
    }
  });

  it('descriptor 缺 feature_count 但有 _tileUrl：inline 写回时仍重建 v= 缓存键', async () => {
    requestRefFCMock.mockResolvedValue({ fc: FC });
    // feature_count 缺失 → isMvt 门判不到，走 inline；但既有 _tileUrl 的 v= 键
    // 不能停在旧 revision，否则后续按瓦片渲染时命中旧缓存。
    const descriptor = { content_revision: 1, mvt_capable: true };
    useHudStore.setState({
      layers: [layer('L1', { _descriptor: descriptor, _tileUrl: 'tiles?ref=dup&v=1' })] as never,
    });

    refreshDatasetLayer('sess-1', 'ref:dup', 11);
    await flush();

    const layers = useHudStore.getState().layers;
    expect(layers).toHaveLength(1);
    expect(layers[0].source).toEqual(FC);
    expect((layers[0]._descriptor as { content_revision?: number }).content_revision).toBe(11);
    expect(layers[0]._tileUrl).toContain('v=11');
  });

  it('无 _tileUrl 的 inline 图层：不凭空写入 tile URL', async () => {
    requestRefFCMock.mockResolvedValue({ fc: FC });
    useHudStore.setState({ layers: [layer('L1', {})] as never });

    refreshDatasetLayer('sess-1', 'ref:dup', 12);
    await flush();

    const [l] = useHudStore.getState().layers;
    expect(l.source).toEqual(FC);
    expect(l._tileUrl).toBeUndefined();
  });

  it('取数拒绝不产生 unhandled rejection', async () => {
    requestRefFCMock.mockRejectedValue(new Error('network down'));
    useHudStore.setState({ layers: [layer('L1', {})] as never });

    const unhandled = await captureUnhandled(() => {
      refreshDatasetLayer('sess-1', 'ref:dup', 2);
    });
    expect(unhandled).toEqual([]);
  });

  it('回调写回抛错不产生 unhandled rejection', async () => {
    requestRefFCMock.mockResolvedValue({ fc: FC });
    useHudStore.setState({ layers: [layer('L1', {})] as never });
    const originalUpdateLayer = useHudStore.getState().updateLayer;
    useHudStore.setState({
      updateLayer: (() => {
        throw new Error('write failed');
      }) as never,
    });
    try {
      const unhandled = await captureUnhandled(() => {
        refreshDatasetLayer('sess-1', 'ref:dup', 3);
      });
      expect(unhandled).toEqual([]);
    } finally {
      useHudStore.setState({ updateLayer: originalUpdateLayer as never });
    }
  });
});
