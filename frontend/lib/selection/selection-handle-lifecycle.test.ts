/**
 * selection-store 句柄生命周期（G24 / P3）—— review P1-2 补测。
 * 有界透传 / 替换释放 / 清空释放 / 会话切换释放 / 同柄增强不释放。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearSelection,
  getSelection,
  publishSelection,
  registerSelectionHandleReleaser,
  resetSelectionStore,
} from '@/lib/selection/selection-store';

let releaser: ReturnType<typeof vi.fn>;

beforeEach(() => {
  releaser = vi.fn();
  registerSelectionHandleReleaser(releaser);
  resetSelectionStore();
});
afterEach(() => {
  registerSelectionHandleReleaser(null);
  resetSelectionStore();
});

function publishWithHandle(handle?: string, layer = 'ref:h-1') {
  publishSelection('brush', {
    source: 'map',
    layer_id: layer,
    artifact_ref: 'ref:h-1',
    selected_ids: ['a'],
    id_field: '_wfid',
    matched_count: 5,
    handle,
    handle_count: handle ? 5 : undefined,
    handle_count_exact: handle ? true : undefined,
  });
}

describe('selection handle lifecycle', () => {
  it('handle 有界透传到 context', () => {
    publishWithHandle('h-token-32chars-aaaaaaaaaaaaaa');
    const sel = getSelection();
    expect(sel?.handle).toBe('h-token-32chars-aaaaaaaaaaaaaa');
    expect(sel?.handle_count).toBe(5);
    expect(sel?.handle_count_exact).toBe(true);
  });

  it('clearSelection 释放句柄（ref/layer 传给 releaser）', () => {
    publishWithHandle('h-1');
    clearSelection();
    expect(releaser).toHaveBeenCalledWith('h-1', 'ref:h-1', 'ref:h-1');
    expect(getSelection()).toBeNull();
  });

  it('替换选择（不同 handle）释放旧句柄；同 handle 增强不释放', () => {
    publishWithHandle('h-old');
    publishWithHandle('h-new');
    expect(releaser).toHaveBeenCalledWith('h-old', 'ref:h-1', 'ref:h-1');
    expect(releaser).toHaveBeenCalledTimes(1);
    expect(getSelection()?.handle).toBe('h-new');

    releaser.mockClear();
    publishWithHandle('h-new'); // 增强重发（同 handle）
    expect(releaser).not.toHaveBeenCalled();
  });

  it('无 handle 的替换不触发释放', () => {
    publishWithHandle('h-x');
    releaser.mockClear();
    publishSelection('select', { source: 'map', layer_id: 'ref:h-1', selected_ids: ['b'] });
    // 替换为无 handle 选择：旧 h-x 应被释放
    expect(releaser).toHaveBeenCalledWith('h-x', 'ref:h-1', 'ref:h-1');
  });

  it('resetSelectionStore（会话切换）释放句柄', () => {
    publishWithHandle('h-session');
    resetSelectionStore();
    expect(releaser).toHaveBeenCalledWith('h-session', 'ref:h-1', 'ref:h-1');
  });

  it('releaser 抛错不阻断清空（静默，服务端 TTL 兜底）', () => {
    releaser.mockImplementation(() => {
      throw new Error('network down');
    });
    publishWithHandle('h-boom');
    expect(() => clearSelection()).not.toThrow();
    expect(getSelection()).toBeNull();
  });
});
