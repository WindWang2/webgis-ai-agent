/**
 * applyLineageToSelection（G24 / P5，A19）—— review P1-2 补测。
 * 真实 selection-store；仅断言 store 语义（不经网络）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/feature-edit', () => ({
  previewFeatureEdit: vi.fn(),
  decideFeatureEditPlan: vi.fn(),
  undoFeatureEdit: vi.fn(),
  redoFeatureEdit: vi.fn(),
}));
vi.mock('@/lib/edit/refresh-dataset-layer', () => ({
  refreshDatasetLayer: vi.fn(),
}));
vi.mock('@/lib/workbench/undo', () => ({
  featureEditCommand: vi.fn(),
}));
vi.mock('@/lib/i18n/t', () => ({
  t: (k: string) => k,
  currentLocale: () => 'zh-CN',
}));

import { applyLineageToSelection } from '@/lib/edit/layer-edit-bridge';
import { getSelection, publishSelection, resetSelectionStore } from '@/lib/selection/selection-store';

function seed(ids: string[], artifactRef = 'ref:layer-a') {
  publishSelection('brush', {
    source: 'map',
    layer_id: 'ref:layer-a',
    artifact_ref: artifactRef,
    selected_ids: ids,
    id_field: '_wfid',
    matched_count: ids.length,
  });
}

beforeEach(() => resetSelectionStore());
afterEach(() => resetSelectionStore());

describe('applyLineageToSelection（A19）', () => {
  it('split：父退役移除 + 两个子嗣并入（同 ref）', () => {
    seed(['wf-p', 'wf-keep']);
    applyLineageToSelection(['wf-p'], ['wf-c1', 'wf-c2'], 'ref:layer-a');
    const sel = getSelection();
    expect(sel?.selected_ids).toEqual(['wf-keep', 'wf-c1', 'wf-c2']);
    expect(sel?.artifact_ref).toBe('ref:layer-a');
  });

  it('merge：多父退役 → 归并子替代', () => {
    seed(['wf-a', 'wf-b', 'wf-c']);
    applyLineageToSelection(['wf-a', 'wf-b'], ['wf-merged'], 'ref:layer-a');
    expect(getSelection()?.selected_ids).toEqual(['wf-c', 'wf-merged']);
  });

  it('纯 create（无退役在选中集）→ 新要素不自动并入', () => {
    seed(['wf-x']);
    applyLineageToSelection([], ['wf-new-1', 'wf-new-2'], 'ref:layer-a');
    expect(getSelection()?.selected_ids).toEqual(['wf-x']);
  });

  it('血缘属于其它 ref → 不跨数据集并集，仅清退役', () => {
    seed(['wf-p', 'wf-q'], 'ref:layer-a');
    applyLineageToSelection(['wf-p'], ['wf-other-child'], 'ref:layer-B');
    expect(getSelection()?.selected_ids).toEqual(['wf-q']);
  });

  it('唯一选中被 split → 选择随血缘替换为子嗣；退役且无新生 → 清空', () => {
    seed(['wf-only']);
    applyLineageToSelection(['wf-only'], ['wf-child'], 'ref:layer-a');
    expect(getSelection()?.selected_ids).toEqual(['wf-child']); // 血缘跟随

    seed(['wf-last']);
    applyLineageToSelection(['wf-last'], [], 'ref:layer-a'); // delete：无新生
    expect(getSelection()).toBeNull();
  });

  it('选择集不含任何退役对象 → 选择不动（编辑与选择无关）', () => {
    seed(['wf-1']);
    const revBefore = getSelection()?.revision;
    applyLineageToSelection(['wf-unrelated'], ['wf-n'], 'ref:layer-a');
    expect(getSelection()?.selected_ids).toEqual(['wf-1']);
    expect(getSelection()?.revision).toBe(revBefore);
  });

  it('无选择时幂等（零副作用）', () => {
    resetSelectionStore();
    expect(() => applyLineageToSelection(['wf-a'], ['wf-b'], 'ref:layer-a')).not.toThrow();
    expect(getSelection()).toBeNull();
  });
});
