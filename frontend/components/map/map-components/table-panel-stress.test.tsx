/**
 * Challenger 2 — Table Panel UI & Interaction Stress Tests
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  resetSelectionStore,
} from '@/lib/selection/selection-store';
import {
  publishViewportContext,
  resetViewportContext,
} from '@/lib/selection/viewport-context';
import { useHudStore } from '@/lib/store/useHudStore';
import { renderComponent } from './index';

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

describe('Table Panel Stress & Edge Cases', () => {
  beforeEach(() => {
    resetSelectionStore();
    resetViewportContext();
    useHudStore.getState().clearLayers?.();
  });

  it('handles regex special characters in filter input safely without crashing', async () => {
    const features = [
      { type: 'Feature', id: '1', properties: { name: 'Normal Park', val: 10 }, geometry: { type: 'Point', coordinates: [104, 30] } },
      { type: 'Feature', id: '2', properties: { name: 'Special [regex] (test) *+?', val: 20 }, geometry: { type: 'Point', coordinates: [104, 30] } },
    ];
    seedLayer(features as never);

    const { container } = render(
      renderComponent(tableComponent({ layerId: 'poi-layer' }), RendererContext) as never,
    );

    const input = screen.getByLabelText('表格行过滤') as HTMLInputElement;
    expect(input).toBeTruthy();

    // Search substring containing raw brackets and wildcards
    act(() => {
      fireEvent.change(input, { target: { value: '[regex] (test) *+?' } });
    });

    expect(screen.getByTestId('table-panel-count').textContent).toBe('1');
    const rows = container.querySelectorAll('[data-testid="table-panel-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('Special [regex]');
  });

  it('sorts numeric columns properly handling nulls and mixed strings', async () => {
    const features = [
      { type: 'Feature', id: '1', properties: { name: 'A', score: 50 }, geometry: { type: 'Point', coordinates: [1, 1] } },
      { type: 'Feature', id: '2', properties: { name: 'B', score: null }, geometry: { type: 'Point', coordinates: [1, 1] } },
      { type: 'Feature', id: '3', properties: { name: 'C', score: 100 }, geometry: { type: 'Point', coordinates: [1, 1] } },
      { type: 'Feature', id: '4', properties: { name: 'D', score: 20 }, geometry: { type: 'Point', coordinates: [1, 1] } },
    ];
    seedLayer(features as never);

    const { container } = render(
      renderComponent(tableComponent({ layerId: 'poi-layer' }), RendererContext) as never,
    );

    const scoreHeader = screen.getByTitle('score 排序');
    expect(scoreHeader).toBeTruthy();

    // Sort ascending: null (empty text "") comes first, then 20, 50, 100
    act(() => {
      fireEvent.click(scoreHeader);
    });
    let rows = container.querySelectorAll('[data-testid="table-panel-row"]');
    expect(rows[0].textContent).toContain('B'); // null
    expect(rows[1].textContent).toContain('D'); // 20
    expect(rows[2].textContent).toContain('A'); // 50
    expect(rows[3].textContent).toContain('C'); // 100

    // Sort descending: 100, 50, 20, null
    act(() => {
      fireEvent.click(scoreHeader);
    });
    rows = container.querySelectorAll('[data-testid="table-panel-row"]');
    expect(rows[0].textContent).toContain('C'); // 100
    expect(rows[1].textContent).toContain('A'); // 50
    expect(rows[2].textContent).toContain('D'); // 20
    expect(rows[3].textContent).toContain('B'); // null
  });

  it('filters rows by viewport bbox when viewport toggle is active', async () => {
    const features = [
      { type: 'Feature', id: 'in-1', properties: { name: 'Inside 1' }, geometry: { type: 'Point', coordinates: [104.05, 30.05] } },
      { type: 'Feature', id: 'in-2', properties: { name: 'Inside 2' }, geometry: { type: 'Point', coordinates: [104.08, 30.08] } },
      { type: 'Feature', id: 'out-1', properties: { name: 'Outside Far' }, geometry: { type: 'Point', coordinates: [116.4, 39.9] } },
      { type: 'Feature', id: 'no-geom', properties: { name: 'No Geometry' }, geometry: null },
    ];
    seedLayer(features as never);

    publishViewportContext([104.0, 30.0, 104.1, 30.1], 10, 0);

    const { container } = render(
      renderComponent(tableComponent({ layerId: 'poi-layer' }), RendererContext) as never,
    );

    expect(screen.getByTestId('table-panel-count').textContent).toBe('4');

    const viewportBtn = screen.getByTitle('只显示当前视口范围内的行');
    act(() => {
      fireEvent.click(viewportBtn);
    });

    expect(screen.getByTestId('table-panel-count').textContent).toBe('2');
    const rows = container.querySelectorAll('[data-testid="table-panel-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Inside 1');
    expect(rows[1].textContent).toContain('Inside 2');
  });
});
