/**
 * table_panel MVT 服务端分页体（Track B · M4）DOM 契约：
 * - MVT 层（无内联 features）不再显示「无内联属性」占位，而是服务端分页体；
 * - 首页渲染 + 「加载更多」续页（游标追加，不重置行）；
 * - 列排序下推服务端（order 参数随点击循环 asc→desc→清除）；
 * - 诚实计数（已显示/匹配/共）与位置 id 披露；
 * - 409 → conflict 横幅 + 重载按钮；行点击发布 source='table' 选择（含 '$id'）；
 * - 查询身份变化（排序）作废 in-flight（旧响应不落屏）。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  getSelection,
  resetSelectionStore,
} from '@/lib/selection/selection-store';
import { useHudStore } from '@/lib/store/useHudStore';
import { renderComponent } from './index';
import { PagedRevisionConflict } from '@/lib/data-plane/paged-features';

vi.mock('@/lib/data-plane/paged-features', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/data-plane/paged-features')>();
  return {
    ...actual,
    fetchFeaturePage: vi.fn(),
  };
});

import { fetchFeaturePage } from '@/lib/data-plane/paged-features';

const mockFetch = vi.mocked(fetchFeaturePage);

function makeFeature(i: number) {
  return {
    type: 'Feature',
    id: i + 1,
    properties: { name: `学校${i}`, students: (i * 37) % 900, type: i % 2 ? '小学' : '中学' },
    geometry: { type: 'Point', coordinates: [104 + (i % 30) * 0.01, 30.6] },
  };
}

function pageOf(features: Array<Record<string, unknown>>, extra: Record<string, unknown> = {}) {
  return {
    features,
    nextCursor: null,
    hasMore: false,
    revision: 7,
    featureCount: 120000,
    bboxMode: null,
    totalMatching: null,
    isComplete: true,
    truncated: false,
    reasonCodes: ['positional_id_projected'],
    idMode: 'positional' as const,
    duplicateIdCount: 0,
    ...extra,
  };
}

function seedMvtLayer() {
  act(() => {
    useHudStore.getState().addLayer?.({
      id: 'mvt-layer',
      name: '成都学校',
      type: 'circle',
      visible: true,
      source: { type: 'vector' },
      _tileUrl: '/api/v1/layers/data/ref:schools/tiles/{z}/{x}/{y}.mvt?session_id=session-paged-test-0001',
      _refId: 'ref:schools',
      _descriptor: { mvt_capable: true, feature_count: 120000, content_revision: 7 },
    } as never);
  });
}

const RendererContext = { spec: null, zoom: 10, centerLat: 30 } as never;

function tableComponent(options: Record<string, unknown>): MapSpecComponent {
  return {
    id: 'table-panel',
    type: 'table_panel',
    enabled: true,
    placement: { mode: 'floating', x: 32, y: 96, width: 420, height: 280 },
    options,
  } as unknown as MapSpecComponent;
}

describe('table_panel server-paged body (MVT layers)', () => {
  beforeEach(() => {
    resetSelectionStore();
    useHudStore.getState().clearLayers?.();
    mockFetch.mockReset();
  });

  it('renders paged body instead of the MVT unavailable placeholder', async () => {
    seedMvtLayer();
    mockFetch.mockResolvedValueOnce(pageOf([makeFeature(0), makeFeature(1)], {
      totalMatching: 2, featureCount: 120000,
    }));
    render(renderComponent(tableComponent({ layerId: 'mvt-layer' }), RendererContext) as never);
    await waitFor(() => {
      expect(screen.getByTestId('table-panel-paged')).toBeTruthy();
    });
    // 不再出现旧的 MVT 占位文案
    expect(screen.queryByText('该图层走矢量瓦片（MVT）通道，无内联属性')).toBeNull();
    // 诚实计数：已显示 2 · 匹配 2 · 共 120000
    const count = screen.getByTestId('table-panel-count').textContent ?? '';
    expect(count).toContain('2');
    expect(count).toContain('120000');
    // 位置 id 披露
    expect(screen.getByTestId('table-panel-positional-note').textContent).toContain('当前数据版本');
    // 首屏一次请求，带 revision guard
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toMatchObject({ refId: 'ref:schools', revision: 7, limit: 200 });
  });

  it('load more appends rows via cursor (no reset)', async () => {
    seedMvtLayer();
    mockFetch.mockResolvedValueOnce(pageOf(
      Array.from({ length: 3 }, (_, i) => makeFeature(i)),
      { nextCursor: 'c2', hasMore: true, isComplete: false },
    ));
    mockFetch.mockResolvedValueOnce(pageOf([makeFeature(3), makeFeature(4)]));
    render(renderComponent(tableComponent({ layerId: 'mvt-layer' }), RendererContext) as never);
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(3);
    });
    fireEvent.click(screen.getByTestId('table-panel-load-more'));
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(5);
    });
    // 续页带 cursor + revision 锚定
    expect(mockFetch.mock.calls[1][0]).toMatchObject({ cursor: 'c2', revision: 7 });
    // 末页后按钮消失
    await waitFor(() => {
      expect(screen.queryByTestId('table-panel-load-more')).toBeNull();
    });
  });

  it('column sort pushes server order param (asc → desc → cleared)', async () => {
    seedMvtLayer();
    mockFetch.mockResolvedValue(pageOf([makeFeature(0)]));
    render(renderComponent(tableComponent({ layerId: 'mvt-layer' }), RendererContext) as never);
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(1);
    });
    // 注意：每次点击后组件回 loading（表头重建）——逐轮重查询按钮。
    fireEvent.click(screen.getByRole('button', { name: /students/ })); // asc
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(1);
      expect(mockFetch.mock.calls.at(-1)?.[0].order).toBe('students');
    });
    fireEvent.click(screen.getByRole('button', { name: /students/ })); // desc
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(1);
      expect(mockFetch.mock.calls.at(-1)?.[0].order).toBe('-students');
    });
    fireEvent.click(screen.getByRole('button', { name: /students/ })); // 清除 → 自然序
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(1);
      expect(mockFetch.mock.calls.at(-1)?.[0].order).toBeUndefined();
    });
  });

  it('row click publishes table selection with $id (positional layer)', async () => {
    seedMvtLayer();
    mockFetch.mockResolvedValueOnce(pageOf([makeFeature(0)]));
    render(renderComponent(tableComponent({ layerId: 'mvt-layer' }), RendererContext) as never);
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(1);
    });
    fireEvent.click(screen.getAllByTestId('table-panel-row')[0]);
    const sel = getSelection();
    expect(sel?.source).toBe('table');
    expect(sel?.layer_id).toBe('mvt-layer');
    expect(sel?.id_field).toBe('$id');
    expect(sel?.selected_ids).toEqual(['1']);
    // 再点同一行 → 清除（toggle 语义）
    fireEvent.click(screen.getAllByTestId('table-panel-row')[0]);
    expect(getSelection()).toBeNull();
  });

  it('409 conflict shows banner with reload; reload refetches with new revision', async () => {
    seedMvtLayer();
    mockFetch.mockRejectedValueOnce(new PagedRevisionConflict(8));
    render(renderComponent(tableComponent({ layerId: 'mvt-layer' }), RendererContext) as never);
    await waitFor(() => {
      expect(screen.getByTestId('table-panel-paged-error').textContent).toContain('版本 8');
    });
    mockFetch.mockResolvedValueOnce(pageOf([makeFeature(0)], { revision: 8 }));
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBe(1);
    });
  });

  it('429 rate limit surfaces honest banner (single auto-retry)', async () => {
    seedMvtLayer();
    const rateErr = Object.assign(new Error('rate'), { status: 429 });
    mockFetch.mockRejectedValueOnce(rateErr).mockRejectedValueOnce(rateErr);
    render(renderComponent(tableComponent({ layerId: 'mvt-layer' }), RendererContext) as never);
    // 第一次 429 → 自动退避重试一次（仍 429）→ 诚实横幅
    await waitFor(() => {
      expect(screen.getByTestId('table-panel-paged-error').textContent).toContain('频繁');
    }, { timeout: 3000 });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});
