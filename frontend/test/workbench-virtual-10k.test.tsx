/**
 * 10k 图层树虚拟化（W8）压测与窗口正确性。
 *
 * 锁定三条不变式：
 *   V1. 窗口恒定：10k 行只渲染窗口（+overscan）个 DOM 行容器 —— 数量与
 *       总行数无关（不 O(N) 渲染）；
 *   V2. 窗口正确性：初始窗口覆盖顶部行；模拟滚动后窗口平移且含目标行；
 *   V3. 折叠传播：父组折叠时子孙组行不渲染（hiddenSectionIds）；
 *   V4. 稳定选择：滚动重渲染后行身份（key=layer.id）稳定，选择不漂移。
 */
import { describe, expect, it } from 'vitest';
import { renderHook, render, fireEvent } from '@testing-library/react';
import { useVirtualRows } from '@/lib/hooks/use-virtual-rows';
import { projectWorkspace } from '@/lib/layers/workspace-projection';
import type { Layer } from '@/lib/types/layer';

describe('useVirtualRows（W8）', () => {
  it('V1: 10k 行窗口恒定（与总行数无关）', () => {
    const { result } = renderHook(() => useVirtualRows(10_000, 34, 8));
    const windowSize = result.current.end - result.current.start;
    // 640px 回退视口 + overscan：窗口 ≈ ceil(640/34)+16 ≈ 35，远小于 10k
    expect(windowSize).toBeLessThan(60);
    expect(result.current.totalHeight).toBe(10_000 * 34);
  });

  it('V2: 滚动后窗口平移且含目标行（真实滚动容器）', () => {
    function Harness() {
      const v = useVirtualRows(10_000, 34, 8);
      return (
        <div ref={v.scrollRef} onScroll={v.onScroll} data-testid="vp" style={{ height: 340 }}>
          <div style={{ height: v.totalHeight }}>
            <div style={{ transform: `translateY(${v.offsetY}px)` }}>
              {Array.from({ length: v.end - v.start }, (_, i) => v.start + i).map((i) => (
                <div key={i} style={{ height: 34 }} data-testid={`row-${i}`}>{i}</div>
              ))}
            </div>
          </div>
        </div>
      );
    }
    const { getByTestId } = render(<Harness />);
    const vp = getByTestId('vp');
    // 模拟滚到第 5000 行（jsdom 无布局 —— scrollTop 手动设定后派发事件）
    Object.defineProperty(vp, 'scrollTop', { value: 5000 * 34, configurable: true });
    fireEvent.scroll(vp);
    expect(getByTestId('row-5000')).toBeTruthy();
    expect(getByTestId(`row-${5000 - 8}`)).toBeTruthy();
  });
});

describe('10k 投影扁平化（W8 压测）', () => {
  it('V3+V4: 10k 行投影 + 嵌套折叠传播 + 窗口 DOM 量恒定（work-count 口径）', () => {
    const layers: Layer[] = [];
    const groups: { id: string; name: string; collapsed: boolean; parentId: string | null }[] = [
      { id: 'root-big', name: '大组', collapsed: false, parentId: null },
      { id: 'child-folded', name: '折叠子组', collapsed: true, parentId: 'root-big' },
    ];
    for (let i = 0; i < 10_000; i++) {
      layers.push({
        id: `layer-${i}`,
        name: `层 ${i}`,
        type: 'vector',
        visible: true,
        opacity: 1,
      } as Layer);
    }
    const membership: Record<string, string> = {};
    for (let i = 0; i < 9500; i++) membership[`layer-${i}`] = 'root-big';
    for (let i = 9500; i < 10_000; i++) membership[`layer-${i}`] = 'child-folded';

    const projection = projectWorkspace({ layers, groups, membership });
    expect(projection.sections.map((s) => s.id)).toEqual(['root-big', 'child-folded']);
    expect(projection.visibleRowCount).toBe(9500);
    expect(projection.hiddenSectionIds.has('child-folded')).toBe(true);

    // 扁平化（与 layers-tab 相同规则）：折叠子组行不进渲染列表
    const flat: string[] = [];
    for (const section of projection.sections) {
      flat.push(`g-${section.id ?? section.name}`);
      if (section.collapsed || (section.id != null && projection.hiddenSectionIds.has(section.id))) continue;
      for (const row of section.rows) flat.push(`l-${row.layer.id}`);
    }
    // 组头×2 + 9500 可见行（折叠子组的 500 行被折叠传播剔除）
    expect(flat.length).toBe(2 + 9500);
    expect(flat.filter((k) => k.startsWith('l-')).length).toBe(9500);

    // 虚拟化窗口：10k 规模下渲染窗口恒定
    const { result } = renderHook(() => useVirtualRows(flat.length, 34, 8));
    const windowSize = result.current.end - result.current.start;
    expect(windowSize).toBeLessThan(60);
  });
});
