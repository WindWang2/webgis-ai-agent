/**
 * QueryResults 结果面板（评审 P2）：labeled 查询可只选 bbox/tags —— 结果
 * 不带 time 坐标时面板不得抛 TypeError 白屏。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryResults } from './query-results';
import type { LabeledWindowResult } from '@/lib/api/lakehouse';

/** labeled 结果（只选 bbox/tags → coords 无 time 轴）。 */
function makeLabeled(overrides: Partial<LabeledWindowResult> = {}): LabeledWindowResult {
  return {
    variables: { temp: [[1, 2], [3, 4]] },
    coords: { y: [10, 20], x: [100, 200] },
    slices: {},
    attrs: {
      crs: 'EPSG:4326', transform: null, nodata: null,
      nodata_per_variable: null, cube_schema_version: 1, dims: ['y', 'x'],
    },
    selection_plan: { cells: 4, touched_chunks: 1, total_chunks: 1, slices: {} },
    ref: 'ref:cube/x',
    ...overrides,
  };
}

describe('QueryResults（labeled 分支）', () => {
  it('无 time 坐标的 labeled 结果：渲染不抛 TypeError', () => {
    const { container } = render(
      <QueryResults
        labeledResult={makeLabeled()}
        onMountToMap={vi.fn()}
        onOpenTimeline={vi.fn()}
      />,
    );
    expect(container).toBeTruthy();
    expect(screen.getByTestId('lakehouse-query-results')).toBeTruthy();
    // 统计摘要照常渲染（变量名 + 上图入口）
    expect(screen.getByTestId('lakehouse-mount-frame')).toBeTruthy();
  });

  it('带 time 坐标的 labeled 结果同样正常渲染', () => {
    render(
      <QueryResults
        labeledResult={makeLabeled({
          coords: { time: ['2026-09-01', '2026-09-02'], y: [10, 20], x: [100, 200] },
          variables: { temp: [[[1, 2], [3, 4]], [[5, 6], [7, 8]]] },
        })}
        onMountToMap={vi.fn()}
        onOpenTimeline={vi.fn()}
      />,
    );
    expect(screen.getByTestId('lakehouse-mount-frame')).toBeTruthy();
  });
});
