/**
 * Workbench V4（Wave 4）：FloatingChrome 交互增量测试。
 * 锁定：snap-to-slot 吸附判定、拖拽落点转 anchor、键盘 resize/折叠/隐藏
 * 提交通道（乐观 override + 去抖 CAS）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { snapTarget } from './floating-chrome';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

// commitComponentPatch mock（组件 mutation 通道的唯一出口）
const commitPatch = vi.fn(async () => ({ status: 'applied' as const }));
vi.mock('@/lib/mapspec/component-mutation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/mapspec/component-mutation')>();
  return {
    ...actual,
    commitComponentPatch: (...args: unknown[]) => commitPatch(...(args as [string, never])),
  };
});

import { FloatingChrome } from './floating-chrome';
import { setComponentPlacementOverride } from '@/lib/mapspec/component-mutation';

describe('snapTarget（纯函数）', () => {
  const parent = { width: 800, height: 600 };

  it('面板中心接近右上槽位 → top-right', () => {
    // 中心 (800, 45)；槽位目标 (800, 0)，距离 45 ≤ 48
    const g = { x: 736, y: 0, width: 128, height: 90 };
    expect(snapTarget(g, parent)).toBe('top-right');
  });

  it('中心远离任何槽位 → null（保持 floating）', () => {
    const g = { x: 320, y: 240, width: 160, height: 120 }; // 中心 (400,300) 画布正中
    expect(snapTarget(g, parent)).toBeNull();
  });

  it('无布局（jsdom 0 尺寸）不吸附', () => {
    const g = { x: 0, y: 0, width: 160, height: 120 };
    expect(snapTarget(g, { width: 0, height: 0 })).toBeNull();
  });

  it('多槽位命中取最近', () => {
    // 中心 (400, 10)：top-center (400,0) 距离 10；top-left (0,0) 距离 400+
    const g = { x: 320, y: -50, width: 160, height: 120 };
    expect(snapTarget(g, parent)).toBe('top-center');
  });
});

function makeComponent(patch: Partial<MapSpecComponent> = {}): MapSpecComponent {
  return {
    id: 'chart_panel-1',
    type: 'chart_panel',
    enabled: true,
    placement: { mode: 'floating', x: 10, y: 10, width: 200, height: 150 },
    ...patch,
  } as MapSpecComponent;
}

function mountChrome(component: MapSpecComponent = makeComponent()) {
  return render(
    <FloatingChrome component={component} title="图表">
      <div>body</div>
    </FloatingChrome>,
  );
}

beforeEach(() => {
  commitPatch.mockClear();
  // 模块级 override store 跨测试持久（同 id）—— 清掉避免几何串场。
  setComponentPlacementOverride('chart_panel-1', null);
});

describe('FloatingChrome · Wave 4 交互', () => {
  it('Ctrl+Arrow 键盘缩放：乐观 override + 提交扩大尺寸', async () => {
    const { container } = mountChrome();
    const titleBar = container.querySelector('[tabindex="0"]') as HTMLElement;
    fireEvent.keyDown(titleBar, { key: 'ArrowRight', ctrlKey: true });
    // 去抖 500ms 后才提交；乐观 override 已记录
    await waitFor(() => expect(commitPatch).toHaveBeenCalled(), { timeout: 1000 });
    const [, patch] = commitPatch.mock.calls[0];
    expect((patch as { placement?: { width?: number } }).placement?.width).toBe(208);
  });

  it('Enter 折叠；Delete 隐藏（enabled=false 提交）', async () => {
    const { container } = mountChrome();
    const titleBar = container.querySelector('[tabindex="0"]') as HTMLElement;
    fireEvent.keyDown(titleBar, { key: 'Enter' });
    await waitFor(() => expect(commitPatch).toHaveBeenCalled());
    expect((commitPatch.mock.calls[0][1] as { placement?: { collapsed?: boolean } }).placement?.collapsed).toBe(true);

    fireEvent.keyDown(titleBar, { key: 'Delete' });
    await waitFor(() => expect(commitPatch).toHaveBeenCalledTimes(2));
    expect((commitPatch.mock.calls[1][1] as { enabled?: boolean }).enabled).toBe(false);
  });

  it('手势几何非有限（jsdom 无真实 clientX）→ 拒绝提交 placement（R2 NaN 守卫）', async () => {
    const { container } = mountChrome(makeComponent({
      placement: { mode: 'floating', x: 720, y: 20, width: 160, height: 120 },
    }));
    const titleBar = screen.getByTestId('floating-chrome-title-bar');
    // jsdom 的 pointer 事件不带有效 clientX（NaN）—— 修复前 NaN 会被
    // Math.round(NaN) 提交进 placement（CSS invalid → 面板消失）；守卫
    // 现在拒绝提交。吸附→anchor 的语义转换由 snapTarget 纯函数测试锁定。
    fireEvent.pointerDown(titleBar, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(titleBar, { pointerId: 1, clientX: 108, clientY: 100 });
    fireEvent.pointerUp(titleBar, { pointerId: 1, clientX: 108, clientY: 100 });
    await new Promise((r) => setTimeout(r, 5));
    expect(commitPatch).not.toHaveBeenCalled();
    // 真实有效几何路径由 floating-chrome.test.tsx 的既有手势用例覆盖。
    void container;
  });
});
