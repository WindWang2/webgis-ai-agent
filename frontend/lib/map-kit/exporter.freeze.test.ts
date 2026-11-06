/**
 * ADR-0283 D3 · 导出冻结快照边界 —— 组合一次性（不混版本）。
 *
 * composeExportSpec 是导出侧 spec 的唯一组合点（W7/ADR-0118）。锁定：
 * 组合完成后，committed MapSpec 对象的后续突变（并发用户编辑/agent 突变）
 * 不得渗入已组合的导出 spec —— 导出全程消费同一冻结组合结果。
 */
import { describe, it, expect } from 'vitest';
import { composeExportSpec } from './exporter';

const hud = {
  layers: [],
  processLayers: {},
  activeFilters: {},
  selectionFilters: {},
  is3D: false,
};

function committedFixture() {
  return {
    layout: { components: [] },
    sources: {},
    layers: [
      { id: 'lyr-a', type: 'fill', source: 'lyr-a', layout: { visibility: 'visible' }, paint: {} },
    ],
  };
}

describe('导出冻结快照边界（ADR-0283 D3）', () => {
  it('组合后 committed 追加图层不渗入导出 spec', async () => {
    const committed = committedFixture();
    const composed = (await composeExportSpec(
      committed as never,
      hud as never,
      {},
      [],
    )) as { layers?: Array<{ id: string }> };
    const frozenCount = composed.layers!.length;
    committed.layers.push({
      id: 'late-mutation',
      type: 'fill',
      source: 'x',
      layout: { visibility: 'visible' },
      paint: {},
    });
    expect(composed.layers!.length).toBe(frozenCount);
    expect(composed.layers!.map((l) => l.id)).not.toContain('late-mutation');
  });

  it('组合后 committed 字段级突变（paint/visibility）不渗入导出 spec', async () => {
    const committed = committedFixture();
    const composed = (await composeExportSpec(
      committed as never,
      hud as never,
      {},
      [],
    )) as { layers?: Array<{ id: string; paint?: Record<string, unknown> }> };
    committed.layers[0].paint = { 'fill-color': '#ff0000' };
    (committed.layers[0] as { layout?: Record<string, unknown> }).layout = {
      visibility: 'none',
    };
    const out = composed.layers![0];
    expect((out.paint as Record<string, unknown> | undefined)?.['fill-color'])
      .toBeUndefined();
    expect((out as { layout?: Record<string, unknown> }).layout?.visibility)
      .toBe('visible');
  });

  it('pending 输入对象组合后突变同样不渗入', async () => {
    const committed = committedFixture();
    const pending: Record<string, { visible?: boolean }> = {};
    const composed = (await composeExportSpec(
      committed as never,
      hud as never,
      pending,
      [],
    )) as { layers?: Array<{ id: string; layout?: Record<string, unknown> }> };
    pending['lyr-a'] = { visible: false };
    expect(composed.layers![0].layout?.visibility).toBe('visible');
  });
});
