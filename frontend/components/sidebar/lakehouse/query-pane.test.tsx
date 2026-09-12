import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import {
  SESSION_ID,
  makeCubeBuildResult,
  makeCubeWindowResult,
  makeObjectRead,
  makeScanResult,
  emptyCatalogPage,
} from '@/test/lakehouse/fixtures';

/**
 * 查询页（P4）：表单执行 → 结果渲染 → 历史记录 → 上图挂层 →
 * object_id → ref 解析动线（含解析失败的诚实提示）。
 */

const lakehouseApi = vi.hoisted(() => ({
  readCubeWindow: vi.fn(),
  readLabeledWindow: vi.fn(),
  scanVector: vi.fn(),
  reviseCube: vi.fn(),
  buildRsCube: vi.fn(),
  getObject: vi.fn(),
}));
vi.mock('@/lib/api/lakehouse', () => ({ lakehouseApi }));

const hudStore = {
  addLayer: vi.fn(),
  theme: 'dark',
};
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign((selector: (s: typeof hudStore) => unknown) => selector(hudStore), {
    getState: () => hudStore,
  }),
}));

const toastStore = { toasts: [], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

// jsdom 无 canvas 2d（gridToDataUrl 如实返回 null）—— 渲染管线在
// raster-pipeline.test.ts 用纯函数覆盖；这里只替换 dataUrl 系（保留统计函数）。
vi.mock('@/lib/map-kit/raster-canvas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/map-kit/raster-canvas')>()),
  gridToRasterSource: (grid: number[][], bbox: [number, number, number, number]) => ({
    image: `data:image/png;base64,frame${grid.length}x${grid[0]?.length ?? 0}`,
    bbox,
  }),
  gridToDataUrl: (grid: number[][]) => `data:image/png;base64,frame${grid.length}`,
}));

import { QueryPane } from './query-pane';

beforeEach(() => {
  vi.clearAllMocks();
});

function fill(ref: string, value: string, label: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('QueryPane — window 查询主链', () => {
  it('执行 window 查询：结果统计 + 时序播放入口 + 历史落账', async () => {
    const result = makeCubeWindowResult();
    // 3 步时序：bands [t][y][x]
    result.bands = {
      band_0: [
        [[1, 2], [3, 4]],
        [[5, 6], [7, 8]],
        [[9, 10], [11, 12]],
      ],
    };
    result.times = ['2026-01-01T00:00:00', '2026-01-02T00:00:00', '2026-01-03T00:00:00'];
    lakehouseApi.readCubeWindow.mockResolvedValue(result);

    render(<QueryPane sessionId={SESSION_ID} />);
    fill('', 'ref:cube/abc', 'cube ref');
    fill('', '0, 3', 'time');
    fireEvent.click(screen.getByTestId('lakehouse-query-submit'));

    await waitFor(() => expect(screen.getByTestId('lakehouse-query-results')).toBeInTheDocument());
    expect(lakehouseApi.readCubeWindow).toHaveBeenCalledWith(
      { session_id: SESSION_ID, ref: 'ref:cube/abc', time: [0, 3] },
      expect.anything(),
    );
    // 统计（首帧 1..4）与播放入口。
    expect(screen.getByText('统计 · band_0')).toBeInTheDocument();
    expect(screen.getByTestId('lakehouse-open-timeline')).toHaveTextContent('3 步');
    // 历史落账。
    await waitFor(() => expect(screen.getByTestId('lakehouse-query-history')).toHaveTextContent('窗口读'));
  });

  it('无切片提交 → 本地校验错误，不出网络请求', async () => {
    render(<QueryPane sessionId={SESSION_ID} />);
    fill('', 'ref:cube/abc', 'cube ref');
    fireEvent.click(screen.getByTestId('lakehouse-query-submit'));
    expect(await screen.findByRole('alert')).toHaveTextContent('至少');
    expect(lakehouseApi.readCubeWindow).not.toHaveBeenCalled();
  });

  it('首帧上图 → HeatmapRasterSource 通道 addLayer', async () => {
    lakehouseApi.readCubeWindow.mockResolvedValue(makeCubeWindowResult());
    render(<QueryPane sessionId={SESSION_ID} />);
    fill('', 'ref:cube/abc', 'cube ref');
    fill('', '0, 1', 'time');
    fireEvent.click(screen.getByTestId('lakehouse-query-submit'));
    const mount = await screen.findByTestId('lakehouse-mount-frame');
    fireEvent.click(mount);
    await waitFor(() => expect(hudStore.addLayer).toHaveBeenCalled());
    const layer = hudStore.addLayer.mock.calls[0][0];
    expect(layer.type).toBe('heatmap');
    expect(layer.source).toMatchObject({ bbox: expect.any(Array) });
    expect(layer.source.image).toMatch(/^data:image\/png/);
  });
});

describe('QueryPane — scan / revise / rs', () => {
  it('矢量扫描 → GeoJSON 直挂图层', async () => {
    lakehouseApi.scanVector.mockResolvedValue(makeScanResult());
    render(<QueryPane sessionId={SESSION_ID} />);
    fireEvent.click(screen.getByRole('radio', { name: '矢量扫描' }));
    fill('', 'ref:fabric-parquet/x', '矢量 ref');
    fill('', '116, 39, 117, 40', 'bbox');
    fireEvent.click(screen.getByTestId('lakehouse-query-submit'));
    await waitFor(() => expect(lakehouseApi.scanVector).toHaveBeenCalled());
    const mount = await screen.findByText('加载至地图（GeoJSON 直挂）');
    fireEvent.click(mount);
    await waitFor(() => expect(hudStore.addLayer).toHaveBeenCalled());
    const layer = hudStore.addLayer.mock.calls[0][0];
    expect(layer.type).toBe('vector');
  });

  it('修订构建 → durable 披露渲染', async () => {
    lakehouseApi.reviseCube.mockResolvedValue(makeCubeBuildResult({ revision_of: 'ref:cube/src' } as never));
    render(<QueryPane sessionId={SESSION_ID} />);
    fireEvent.click(screen.getByRole('radio', { name: '修订' }));
    fill('', 'ref:cube/src', 'cube ref');
    fill('', 'band_0', '修订 band');
    fill('', 'ref:fabric-parquet/new', '修订来源');
    fireEvent.click(screen.getByTestId('lakehouse-query-submit'));
    await waitFor(() => expect(screen.getByText('构建回执')).toBeInTheDocument());
    expect(screen.getByText('published')).toBeInTheDocument();
  });
});

describe('QueryPane — object_id 动线', () => {
  it('manifest payload.ref 存在 → 表单回填', async () => {
    lakehouseApi.getObject.mockResolvedValue(
      makeObjectRead({
        manifest: {
          ...makeObjectRead().manifest,
          payload: { ref: 'ref:cube/deadbeef' },
        },
      }),
    );
    render(<QueryPane sessionId={SESSION_ID} objectIdHint="aa" />);
    const refInput = await screen.findByLabelText('cube ref');
    await waitFor(() => expect(refInput).toHaveValue('ref:cube/deadbeef'));
  });

  it('manifest 无 ref → 诚实提示不臆测', async () => {
    lakehouseApi.getObject.mockResolvedValue(makeObjectRead());
    render(<QueryPane sessionId={SESSION_ID} objectIdHint="aa" />);
    await waitFor(() =>
      expect(screen.getByText(/未携带 cube ref/)).toBeInTheDocument(),
    );
  });
});

// 防止空目录 mock 未用（searchCatalog 由 tab 级测试覆盖；此处仅对齐模块形状）。
void emptyCatalogPage;
