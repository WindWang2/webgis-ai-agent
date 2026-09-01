/**
 * SelectionContext V4（Runtime V4 / ADR-0091）契约测试：
 * - 谓词有界（bbox 数值合法 / in values ≤ 上限）；
 * - id 过滤投影（$id → ['id']；属性字段 → ['get', f]；类别投影优先）；
 * - epoch guard（会话切换后迟到语义）；
 * - extent_change 不抢占选择上下文；
 * - kind 随发布记录。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import {
  clearSelection,
  getSelection,
  getSelectionEpoch,
  getSelectionIdFilter,
  getSelectionIdFilterExpression,
  publishSelection,
  resetSelectionStore,
  selectionEvents,
  MAX_PREDICATE_VALUES,
} from './selection-store';
import { resetViewportContext, publishViewportContext, getViewportContext } from './viewport-context';

describe('selection store V4', () => {
  beforeEach(() => {
    resetSelectionStore();
    resetViewportContext();
  });

  it('records the publication kind on the context', () => {
    publishSelection('brush', { source: 'map', layer_id: 'l1', selected_ids: ['a'] });
    expect(getSelection()?.kind).toBe('brush');
    publishSelection('filter', { source: 'chart', layer_id: 'l1', selected_categories: ['x'] });
    expect(getSelection()?.kind).toBe('filter');
  });

  it('bounds the in-predicate values', () => {
    publishSelection('brush', {
      source: 'map',
      layer_id: 'l1',
      selected_ids: [],
      predicate: { kind: 'in', field: 'district', values: Array.from({ length: 100 }, (_, i) => `d${i}`) },
    });
    expect(getSelection()?.predicate?.kind).toBe('in');
    expect((getSelection()?.predicate as { values: string[] }).values.length).toBe(MAX_PREDICATE_VALUES);
  });

  it('drops malformed bbox predicates', () => {
    publishSelection('brush', {
      source: 'map', layer_id: 'l1',
      predicate: { kind: 'bbox', bbox: [NaN, 0, 1, 2] },
    });
    expect(getSelection()?.predicate).toBeUndefined();
  });

  it('carries matched_count and truncation-safe payload', () => {
    publishSelection('brush', {
      source: 'map', layer_id: 'l1',
      selected_ids: ['a'], id_field: 'OBJECTID',
      matched_count: 512, bbox: [104, 30.6, 104.1, 30.7],
      predicate: { kind: 'bbox', bbox: [104, 30.6, 104.1, 30.7] },
    });
    const sel = getSelection();
    expect(sel?.matched_count).toBe(512);
    expect(sel?.id_field).toBe('OBJECTID');
  });

  it('compiles id filter expressions ($id vs property field)', () => {
    publishSelection('brush', {
      source: 'map', layer_id: 'l1', selected_ids: ['a', 'b'], id_field: '$id',
    });
    expect(getSelectionIdFilterExpression('l1')).toEqual(['in', ['to-string', ['id']], ['literal', ['a', 'b']]]);
    publishSelection('select', {
      source: 'table', layer_id: 'l2', selected_ids: ['x'], id_field: 'fid',
    });
    expect(getSelectionIdFilterExpression('l2')).toEqual(['in', ['to-string', ['get', 'fid']], ['literal', ['x']]]);
    expect(getSelectionIdFilter('l1')).toBeNull(); // layer mismatch
  });

  it('category projection wins over id projection when both present', () => {
    publishSelection('select', {
      source: 'chart', layer_id: 'l1',
      selected_ids: ['a'], id_field: 'fid',
      selected_categories: ['武侯区'], filter_field: 'district',
    });
    expect(getSelectionIdFilter('l1')).toBeNull(); // 类别通道优先
  });

  it('epoch bumps on session reset (late async publications can be detected)', () => {
    const before = getSelectionEpoch();
    resetSelectionStore();
    expect(getSelectionEpoch()).toBeGreaterThan(before);
  });

  it('extent_change never clobbers the selection context', () => {
    publishSelection('select', { source: 'map', layer_id: 'l1', selected_ids: ['a'] });
    publishSelection('extent_change', { source: 'map', layer_id: '' });
    expect(getSelection()?.selected_ids).toEqual(['a']);
    // 事件面有观测记录（诊断），但 current 不变。
    expect(selectionEvents().some((e) => e.kind === 'extent_change')).toBe(true);
  });

  it('viewport context is a sibling store (dedup + reset)', () => {
    // debounceMs=0 → 立即提交（同步路径）。
    expect(publishViewportContext([104, 30.6, 104.1, 30.7], 10.5, 0)).toBe(true);
    // 相同指纹 → 抑制（false = 未产生新发布）。
    expect(publishViewportContext([104, 30.6, 104.1, 30.7], 10.5, 0)).toBe(false);
    expect(getViewportContext()?.bbox).toEqual([104, 30.6, 104.1, 30.7]);
    resetViewportContext(); // flush + epoch bump + clear
    expect(getViewportContext()).toBeNull();
  });

  it('clear_selection keeps working after V4 extensions', () => {
    publishSelection('brush', { source: 'map', layer_id: 'l1', selected_ids: ['a'] });
    clearSelection();
    expect(getSelection()).toBeNull();
  });
});
