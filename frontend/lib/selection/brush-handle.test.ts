/**
 * brush-handle（G24 / P3）域内契约 —— review P1-2 补测：
 * - 成功：store 补发布（handle/handle_count/权威 matched_count）+ IR 补发
 *   selection_handle（Agent 获取通道，A26 前端半边）；
 * - A17 迟到守卫 1：epoch 前进（会话切换）→ 零补发布零 IR；
 * - A17 迟到守卫 2：store revision 前进（更新选择）→ 零补发布零 IR；
 * - 建柄失败 → null（调用方保持谓词选择，零 mutation）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const createMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/selection-handles', () => ({
  createSelectionHandle: createMock,
}));
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: () => ({ sessionId: 'sess-brush-0001', ownerToken: 'tok' }),
}));
const emitMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/interaction/bus', () => ({
  emitFeatureSelected: emitMock,
}));

import {
  augmentBrushSelectionWithHandle,
} from '@/lib/selection/brush-handle';
import {
  getSelection,
  getSelectionEpoch,
  publishSelection,
  resetSelectionStore,
} from '@/lib/selection/selection-store';

const BASE = {
  sessionId: 'sess-brush-0001',
  refId: 'ref:brush-1',
  layerId: 'ref:brush-1',
  bbox: [104.0, 30.0, 104.6, 31.0] as [number, number, number, number],
  revision: 7,
  selectedIds: ['a', 'b'],
  idField: '_wfid',
  matchedCountRendered: 300,
  publishedRevision: 0,
  epochAtPublish: 0,
};

beforeEach(() => {
  createMock.mockReset();
  emitMock.mockReset();
  resetSelectionStore();
  // 初始有界发布（map-panel onUp 同步路径的等价物）
  publishSelection('brush', {
    source: 'map',
    layer_id: BASE.layerId,
    artifact_ref: BASE.refId,
    selected_ids: BASE.selectedIds,
    id_field: BASE.idField,
    predicate: { kind: 'bbox', bbox: BASE.bbox },
    matched_count: 300,
    bbox: BASE.bbox,
  });
  BASE.publishedRevision = getSelection()?.revision ?? 0;
  BASE.epochAtPublish = getSelectionEpoch(); // resetSelectionStore 已 bump，须取实时值
});
afterEach(() => {
  resetSelectionStore();
});

describe('augmentBrushSelectionWithHandle', () => {
  it('成功：补发布 handle/权威计数 + IR selection_handle', async () => {
    createMock.mockResolvedValueOnce({
      handle: 'h-abc', count: 300, count_exact: true, revision: 7,
      scope: {}, ttl_seconds: 600,
    });
    const out = await augmentBrushSelectionWithHandle({ ...BASE });
    expect(out).toEqual({ status: 'augmented', handle: 'h-abc', count: 300, countExact: true });
    const sel = getSelection();
    expect(sel?.handle).toBe('h-abc');
    expect(sel?.handle_count).toBe(300);
    expect(sel?.handle_count_exact).toBe(true);
    expect(sel?.matched_count).toBe(300);
    expect(createMock).toHaveBeenCalledTimes(1);
    expect(createMock.mock.calls[0][0].scope).toEqual({ kind: 'bbox', bbox: BASE.bbox });
    expect(createMock.mock.calls[0][0].revision).toBe(7);
    expect(emitMock).toHaveBeenCalledTimes(1);
    expect(emitMock.mock.calls[0][0].selectionHandle).toBe('h-abc');
    expect(emitMock.mock.calls[0][0].matchedCount).toBe(300);
  });

  it('A17 守卫 1：会话切换（epoch 前进）→ 零补发布零 IR', async () => {
    createMock.mockImplementationOnce(async () => {
      resetSelectionStore(); // 模拟在途时会话切换（epoch bump）
      return { handle: 'h-x', count: 5, count_exact: true, revision: 7, scope: {}, ttl_seconds: 600 };
    });
    const out = await augmentBrushSelectionWithHandle({ ...BASE });
    expect(out).toEqual({ status: 'discarded' });
    expect(emitMock).not.toHaveBeenCalled();
    expect(getSelection()?.handle).toBeUndefined();
  });

  it('A17 守卫 2：已有更新选择（revision 前进）→ 迟到结果被丢弃', async () => {
    createMock.mockImplementationOnce(async () => {
      publishSelection('select', {
        source: 'map', layer_id: 'other-layer', selected_ids: ['z'],
      });
      return { handle: 'h-y', count: 5, count_exact: true, revision: 7, scope: {}, ttl_seconds: 600 };
    });
    const out = await augmentBrushSelectionWithHandle({ ...BASE });
    expect(out).toEqual({ status: 'discarded' });
    expect(emitMock).not.toHaveBeenCalled();
    // 新选择未被迟到的框选增强污染
    expect(getSelection()?.layer_id).toBe('other-layer');
    expect(getSelection()?.handle).toBeUndefined();
  });

  it('建柄失败 → {status:"failed"}（R2-1：可反馈；谓词选择保持，零 IR）', async () => {
    createMock.mockRejectedValueOnce(new Error('conflict'));
    const before = getSelection();
    const out = await augmentBrushSelectionWithHandle({ ...BASE });
    expect(out).toEqual({ status: 'failed' });
    const after = getSelection();
    expect(after?.revision).toBe(before?.revision);
    expect(after?.handle).toBeUndefined();
    expect(emitMock).not.toHaveBeenCalled();
  });

  it('count 不可证（count_exact=false）也如实补发布（不作假）', async () => {
    createMock.mockResolvedValueOnce({
      handle: 'h-unk', count: null, count_exact: false, revision: 7, scope: {}, ttl_seconds: 600,
    });
    const out = await augmentBrushSelectionWithHandle({ ...BASE });
    expect(out.status).toBe('augmented');
    if (out.status !== 'augmented') return;
    expect(out.countExact).toBe(false);
    expect(getSelection()?.handle_count_exact).toBeUndefined();
    expect(getSelection()?.matched_count).toBe(300); // 渲染面计数兜底披露
  });
});
