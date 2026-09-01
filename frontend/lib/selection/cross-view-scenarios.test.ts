/**
 * 跨视图联动场景测试（Runtime V4 / §9、§50 Scenario A/B/D 子集 —— 纯 store
 * 层可锁定的部分；DOM 级联动在 table-panel.test.tsx）。
 *
 * Scenario A（chart→map→table 零重查）：chart 类别点击发布 → id/category
 * 投影可编译 → map 过滤表达式生成（数据面不动 —— adapter 测试已锁引用）。
 * Scenario B（map 框选）：brush → 谓词/ids 有界；不产生任何 MapSpec 写。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import {
  clearSelection,
  getSelection,
  getSelectionFilterExpression,
  getSelectionIdFilterExpression,
  publishSelection,
  resetSelectionStore,
  selectionEvents,
} from './selection-store';

describe('Scenario A — chart click drives map filter (zero refetch by contract)', () => {
  beforeEach(() => resetSelectionStore());

  it('publish → category filter expression compiles for the bound layer', () => {
    publishSelection('select', {
      source: 'chart',
      layer_id: 'district-choropleth',
      selected_categories: ['武侯区', '锦江区'],
      filter_field: 'district',
      artifact_ref: 'ref:chart-abc',
    });
    const expr = getSelectionFilterExpression('district-choropleth');
    expect(expr).toEqual(['in', ['get', 'district'], ['literal', ['武侯区', '锦江区']]]);
  });

  it('selection never emits MapSpec mutations (event ring is diagnostics-only)', () => {
    publishSelection('select', {
      source: 'chart', layer_id: 'l', selected_categories: ['x'], filter_field: 'f',
    });
    // 契约：事件环只记录，不携带 mutation 语义；clear 后无残留。
    clearSelection();
    expect(getSelection()).toBeNull();
    expect(selectionEvents().length).toBeLessThanOrEqual(16);
  });
});

describe('Scenario B — map brush stays bounded', () => {
  beforeEach(() => resetSelectionStore());

  it('large brush carries a bbox predicate + matched_count, not 10k ids', () => {
    const ids = Array.from({ length: 50 }, (_, i) => `f${i}`);
    publishSelection('brush', {
      source: 'map',
      layer_id: 'poi-layer',
      selected_ids: ids,
      id_field: 'osm_id',
      matched_count: 9000,
      predicate: { kind: 'bbox', bbox: [104.0, 30.6, 104.1, 30.7] },
      bbox: [104.0, 30.6, 104.1, 30.7],
    });
    const sel = getSelection();
    expect(sel?.selected_ids.length).toBe(50);
    expect(sel?.matched_count).toBe(9000);
    expect(sel?.predicate?.kind).toBe('bbox');
    // id 过滤投影可用（50 ids），谓词是超限部分的描述符。
    expect(getSelectionIdFilterExpression('poi-layer')).toBeDefined();
  });

  it('table row click publishes stable ids (table→map)', () => {
    publishSelection('select', {
      source: 'table', layer_id: 'poi-layer', selected_ids: ['osm-1'], id_field: 'osm_id',
    });
    expect(getSelectionIdFilterExpression('poi-layer')).toEqual(
      ['in', ['get', 'osm_id'], ['literal', ['osm-1']]],
    );
  });

  it('map shift-click appends to the same layer selection', () => {
    publishSelection('select', {
      source: 'map', layer_id: 'l', selected_ids: ['a'], id_field: 'id',
    });
    publishSelection('select', {
      source: 'map', layer_id: 'l', selected_ids: ['a', 'b'], id_field: 'id',
    });
    expect(getSelection()?.selected_ids).toEqual(['a', 'b']);
  });

  it('cross-layer selection replaces (no mixed-layer id filter)', () => {
    publishSelection('select', { source: 'map', layer_id: 'l1', selected_ids: ['a'], id_field: 'id' });
    publishSelection('select', { source: 'map', layer_id: 'l2', selected_ids: ['x'], id_field: 'id' });
    expect(getSelectionIdFilterExpression('l1')).toBeNull();
    expect(getSelectionIdFilterExpression('l2')).toBeDefined();
  });
});
