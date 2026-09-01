/**
 * table_panel 渲染器（§10/§9）DOM 契约：
 * - 虚拟化：大表 DOM 行数 ∝ 视口（与总行数无关）；
 * - table→map：行点击发布 source='table' 选择；
 * - chart→table：类别选择 → 行过滤；
 * - 降级：未绑定 / 数据不可用不崩 chrome。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  clearSelection,
  getSelection,
  publishSelection,
  resetSelectionStore,
} from '@/lib/selection/selection-store';
import { useHudStore } from '@/lib/store/useHudStore';
import { renderComponent } from './index';

// FloatingChrome 是纯 DOM 组件（无 maplibre 依赖）—— 直接渲染。

function tableComponent(options: Record<string, unknown>): MapSpecComponent {
  return {
    id: 'table-panel',
    type: 'table_panel',
    enabled: true,
    placement: { mode: 'floating', x: 32, y: 96, width: 420, height: 280 },
    options,
  } as unknown as MapSpecComponent;
}

function seedLayer(features: Array<Record<string, unknown>>) {
  const store = useHudStore.getState();
  act(() => {
    store.addLayer?.({
      id: 'poi-layer',
      name: 'POI',
      type: 'circle',
      visible: true,
      source: { type: 'FeatureCollection', features },
    } as never);
  });
}

const RendererContext = { spec: null, zoom: 10, centerLat: 30 } as never;

describe('table_panel renderer', () => {
  beforeEach(() => {
    resetSelectionStore();
    useHudStore.getState().clearLayers?.();
  });

  it('renders virtualized rows (DOM ∝ viewport, not total)', () => {
    const features = Array.from({ length: 5000 }, (_, i) => ({
      type: 'Feature',
      id: i,
      properties: { osm_id: i, name: `POI ${i}` },
      geometry: { type: 'Point', coordinates: [104, 30] },
    }));
    seedLayer(features as never);
    const { container } = render(
      renderComponent(tableComponent({ layerId: 'poi-layer' }), RendererContext) as never,
    );
    const rows = container.querySelectorAll('[data-testid="table-panel-row"]');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(60); // 5000 行 → ~40 DOM 行（视口+overscan）
    expect(screen.getByTestId('table-panel-count').textContent).toContain('5000');
  });

  it('row click publishes table selection with stable ids (table→map)', async () => {
    const features = [
      { type: 'Feature', properties: { osm_id: 1, name: 'A' }, geometry: { type: 'Point', coordinates: [1, 1] } },
      { type: 'Feature', properties: { osm_id: 2, name: 'B' }, geometry: { type: 'Point', coordinates: [1, 1] } },
    ];
    seedLayer(features as never);
    const { container } = render(
      renderComponent(tableComponent({ layerId: 'poi-layer' }), RendererContext) as never,
    );
    const row = container.querySelector('[data-testid="table-panel-row"]') as HTMLElement;
    expect(row).toBeTruthy();
    act(() => {
      row.click();
    });
    const sel = getSelection();
    expect(sel?.source).toBe('table');
    expect(sel?.layer_id).toBe('poi-layer');
    expect(sel?.selected_ids).toHaveLength(1);
    // Toggle off on second click.
    const sameRow = container.querySelector('[data-testid="table-panel-row"]') as HTMLElement;
    act(() => {
      sameRow.click();
    });
    expect(getSelection()).toBeNull();
  });

  it('chart category selection filters rows (chart→table §9.5)', async () => {
    const features = [
      { type: 'Feature', properties: { district: '武侯区', v: 1 }, geometry: { type: 'Point', coordinates: [1, 1] } },
      { type: 'Feature', properties: { district: '锦江区', v: 2 }, geometry: { type: 'Point', coordinates: [1, 1] } },
    ];
    seedLayer(features as never);
    const { container } = render(
      renderComponent(tableComponent({ layerId: 'poi-layer' }), RendererContext) as never,
    );
    expect(container.querySelectorAll('[data-testid="table-panel-row"]').length).toBe(2);
    act(() => {
      publishSelection('select', {
        source: 'chart',
        layer_id: 'poi-layer',
        selected_categories: ['武侯区'],
        filter_field: 'district',
      });
    });
    await waitFor(() => {
      expect(container.querySelectorAll('[data-testid="table-panel-row"]').length).toBe(1);
    });
  });

  it('unbound panel degrades honestly (no crash)', async () => {
    const { container } = render(
      renderComponent(tableComponent({}), RendererContext) as never,
    );
    expect(container.textContent).toContain('未绑定数据');
  });
});
