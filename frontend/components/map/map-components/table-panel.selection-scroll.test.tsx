/**
 * table_panel 选中集视图：map→table 滚动定位必须查 view.rows（选中集行序），
 * 不得继续查 table.rows —— 否则首行定位按另一套行序计算（静默错位/失效）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { publishSelection, resetSelectionStore } from '@/lib/selection/selection-store';
import { useHudStore } from '@/lib/store/useHudStore';
import { renderComponent } from './index';

const selRows = vi.hoisted(() => ({ rows: [] as Array<{ rowId: string; props: Record<string, unknown> }> }));

vi.mock('@/lib/map-components/selection-table-data', () => ({
  useSelectionHandleTable: () => ({
    status: 'ready',
    rows: selRows.rows,
    columns: ['name'],
    nextCursor: null,
    count: selRows.rows.length,
    countExact: true,
    loadingMore: false,
    reasonCodes: [],
    loadMore: () => {},
    reload: () => {},
  }),
}));

vi.mock('@/lib/data-plane/paged-features', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/data-plane/paged-features')>();
  return { ...actual, fetchFeaturePage: vi.fn() };
});

import { fetchFeaturePage } from '@/lib/data-plane/paged-features';
const mockFetch = vi.mocked(fetchFeaturePage);

function tableComponent(): MapSpecComponent {
  return {
    id: 'table-panel-scroll',
    type: 'table_panel',
    enabled: true,
    placement: { mode: 'floating', x: 32, y: 96, width: 420, height: 280 },
    options: { layerId: 'mvt-layer' },
  } as unknown as MapSpecComponent;
}

function seedMvtLayer() {
  act(() => {
    useHudStore.getState().addLayer?.({
      id: 'mvt-layer',
      name: 'POI',
      type: 'circle',
      visible: true,
      source: { type: 'vector' },
      _tileUrl: '/api/v1/layers/data/ref:scroll/tiles/{z}/{x}/{y}.mvt?session_id=session-scroll-test',
      _refId: 'ref:scroll',
      _descriptor: { mvt_capable: true, feature_count: 120000, content_revision: 7 },
    } as never);
  });
}

function pageOf() {
  return {
    features: Array.from({ length: 10 }, (_, i) => ({
      type: 'Feature',
      id: i,
      properties: { id: i, name: `row-${i}` },
      geometry: { type: 'Point', coordinates: [104, 30] },
    })),
    nextCursor: null,
    hasMore: false,
    revision: 7,
    featureCount: 10,
    bboxMode: null,
    totalMatching: null,
    isComplete: true,
    truncated: false,
    reasonCodes: [],
    idMode: 'positional' as const,
    duplicateIdCount: 0,
  };
}

describe('table_panel 选中集视图滚动定位', () => {
  beforeEach(() => {
    resetSelectionStore();
    useHudStore.getState().clearLayers?.();
    selRows.rows = [];
    mockFetch.mockReset();
    mockFetch.mockResolvedValue(pageOf() as never);
  });

  it('选中集视图活跃时按 view.rows 定位首行（不是 table.rows）', async () => {
    seedMvtLayer();
    // 选中集行序与表格行序不同：id=8 在选中集里是首行，在 table 里是第 9 行。
    selRows.rows = [
      { rowId: '8', props: { id: 8, name: 'row-8' } },
      { rowId: '0', props: { id: 0, name: 'row-0' } },
      { rowId: '3', props: { id: 3, name: 'row-3' } },
    ];
    act(() => {
      publishSelection('select', {
        source: 'map',
        layer_id: 'mvt-layer',
        selected_ids: ['7'],
        handle: 'h1',
        handle_count: 3,
        handle_count_exact: true,
      });
    });

    render(renderComponent(tableComponent(), {} as never) as never);
    await waitFor(() => {
      expect(screen.getAllByTestId('table-panel-row').length).toBeGreaterThan(0);
    });
    const toggle = screen.getByTestId('table-panel-selection-view-toggle');
    act(() => {
      toggle.click();
    });

    // 选中集视图活跃后，新选择落到 id=8（选中集首行）。
    act(() => {
      publishSelection('select', {
        source: 'map',
        layer_id: 'mvt-layer',
        selected_ids: ['8'],
        handle: 'h1',
        handle_count: 3,
        handle_count_exact: true,
      });
    });

    await waitFor(() => {
      // 修复前查 table.rows → index 8 → scrollTop = 8*28-120 = 104。
      expect(screen.getByTestId('table-panel-scroll').scrollTop).toBe(0);
    });
  });
});
