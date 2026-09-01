import { describe, expect, it, beforeEach } from 'vitest';
import {
  clearSelection,
  getSelection,
  getSelectionFilter,
  getSelectedCategories,
  publishSelection,
  resetSelectionStore,
  selectionEvents,
  MAX_SELECTED_CATEGORIES,
  MAX_SELECTED_IDS,
} from './selection-store';

/**
 * SelectionContext（Workspace V2 / Goal D）—— 统一跨视图选择契约。
 *
 * 不变量：
 * - 单一选择上下文（map/chart/table/legend 同源发布/订阅）；
 * - 有界：ids ≤ 50、categories ≤ 20、properties ≤ 8 标量；
 * - transient：clear/重置不留残迹；事件环有界；
 * - 过滤投影只在 filter_field 在场时成立（缺席 → 状态高亮降级）。
 */
describe('selection store', () => {
  beforeEach(() => {
    resetSelectionStore();
  });

  it('publishes a unified select context from chart', () => {
    publishSelection('select', {
      source: 'chart',
      layer_id: 'district-choropleth',
      selected_categories: ['武侯区'],
      filter_field: 'district',
      artifact_ref: 'ref:chart-1',
    });
    const sel = getSelection();
    expect(sel).toMatchObject({
      source: 'chart',
      layer_id: 'district-choropleth',
      selected_categories: ['武侯区'],
      filter_field: 'district',
      artifact_ref: 'ref:chart-1',
    });
    expect(getSelectedCategories('district-choropleth')).toEqual(['武侯区']);
    expect(getSelectedCategories('other-layer')).toBeNull();
  });

  it('map selection carries bounded scalar properties (no geometry)', () => {
    publishSelection('select', {
      source: 'map',
      layer_id: 'l1',
      selected_ids: ['f1'],
      properties: {
        name: '武侯区第一小学',
        count: 12,
        active: true,
        nested: { deep: [1, 2, 3] }, // 非标量 → 剔除
        coordinates: 'SRID=...', // 标量字符串保留（调用方不给几何即可）
      },
    });
    const props = getSelection()?.properties;
    expect(props).toMatchObject({ name: '武侯区第一小学', count: 12, active: true });
    expect(props && !('nested' in props)).toBe(true);
    expect(JSON.stringify(props).length).toBeLessThan(400);
  });

  it('bounds selected ids and categories', () => {
    publishSelection('select', {
      source: 'table',
      layer_id: 'l1',
      selected_ids: Array.from({ length: 200 }, (_, i) => `f${i}`),
      selected_categories: Array.from({ length: 50 }, (_, i) => `c${i}`),
    });
    const sel = getSelection();
    expect(sel?.selected_ids.length).toBe(MAX_SELECTED_IDS);
    expect(sel?.selected_categories.length).toBe(MAX_SELECTED_CATEGORIES);
  });

  it('selection filter projection requires layer match + filter field + categories', () => {
    publishSelection('select', {
      source: 'chart',
      layer_id: 'l1',
      selected_categories: ['武侯区'],
    });
    expect(getSelectionFilter('l1')).toBeNull(); // 无 filter_field → 状态高亮降级
    publishSelection('select', {
      source: 'chart',
      layer_id: 'l1',
      selected_categories: ['武侯区'],
      filter_field: 'district',
    });
    expect(getSelectionFilter('l1')).toEqual({ field: 'district', categories: ['武侯区'] });
    expect(getSelectionFilter('l2')).toBeNull();
  });

  it('clear_selection empties the context and records the event', () => {
    publishSelection('select', { source: 'map', layer_id: 'l1' });
    clearSelection();
    expect(getSelection()).toBeNull();
    expect(getSelectedCategories('l1')).toBeNull();
    expect(selectionEvents().at(-1)?.kind).toBe('clear_selection');
  });

  it('event ring stays bounded', () => {
    for (let i = 0; i < 40; i += 1) {
      publishSelection('select', { source: 'map', layer_id: `l${i % 3}` });
    }
    expect(selectionEvents().length).toBeLessThanOrEqual(16);
  });

  it('revision is monotonic per publication', () => {
    publishSelection('select', { source: 'map', layer_id: 'l1' });
    const r1 = getSelection()?.revision;
    publishSelection('select', { source: 'map', layer_id: 'l1' });
    const r2 = getSelection()?.revision;
    expect(r2).toBeGreaterThan(r1 ?? 0);
  });
});
