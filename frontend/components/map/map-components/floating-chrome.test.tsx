/**
 * FloatingChrome 手势提交语义测试（D4）：
 * - 拖拽/缩放手势 pointerup 时恰好一次 commitComponentPatch（无逐帧提交）；
 * - pointermove 期间零提交；原地点击（无位移）不提交；
 * - 折叠 → placement.collapsed；隐藏 → enabled=false；复位 → anchor 缺省。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import React from 'react';

vi.mock('@/lib/mapspec/component-mutation', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('@/lib/mapspec/component-mutation')
  >();
  return { ...actual, commitComponentPatch: vi.fn(() => Promise.resolve()) };
});

import { FloatingChrome } from './floating-chrome';
import { commitComponentPatch, setComponentPlacementOverride } from '@/lib/mapspec/component-mutation';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

const commitMock = vi.mocked(commitComponentPatch);

const FLOATING_COMP: MapSpecComponent = {
  id: 'comp-1',
  type: 'chart_panel',
  enabled: true,
  placement: { mode: 'floating', x: 0, y: 0, width: 320, height: 240 },
};

// jsdom（26.x）没有 PointerEvent 构造器，fireEvent.pointer* 的
// clientX/pointerId init 不落盘 —— 用 defineProperty 的原生 Event 补齐。
function pointerEvent(
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  opts: { pointerId?: number; clientX?: number; clientY?: number },
): Event {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'pointerId', { value: opts.pointerId ?? 1 });
  Object.defineProperty(ev, 'clientX', { value: opts.clientX ?? 0 });
  Object.defineProperty(ev, 'clientY', { value: opts.clientY ?? 0 });
  return ev;
}

function firePointer(
  node: Element,
  type: 'pointerdown' | 'pointermove' | 'pointerup',
  opts: { pointerId?: number; clientX?: number; clientY?: number },
): boolean {
  return fireEvent(node, pointerEvent(type, opts));
}

function renderChrome(component: MapSpecComponent = FLOATING_COMP) {
  return render(
    <FloatingChrome component={component} title="图表面板" testId="fc-test">
      <div>面板内容</div>
    </FloatingChrome>,
  );
}

beforeEach(() => {
  commitMock.mockClear();
  setComponentPlacementOverride('comp-1', null);
});

describe('FloatingChrome — 拖拽手势', () => {
  it('pointerdown → pointermove（无 pointerup）期间零提交', () => {
    renderChrome();
    const titleBar = screen.getByTestId('fc-test-title-bar');
    firePointer(titleBar, 'pointerdown', { pointerId: 1, clientX: 100, clientY: 100 });
    firePointer(titleBar, 'pointermove', { pointerId: 1, clientX: 130, clientY: 120 });
    firePointer(titleBar, 'pointermove', { pointerId: 1, clientX: 160, clientY: 150 });
    expect(commitMock).not.toHaveBeenCalled();
  });

  it('拖拽收尾：pointerup 时恰好一次提交，携带最终 placement', async () => {
    renderChrome();
    const titleBar = screen.getByTestId('fc-test-title-bar');
    firePointer(titleBar, 'pointerdown', { pointerId: 1, clientX: 100, clientY: 100 });
    firePointer(titleBar, 'pointermove', { pointerId: 1, clientX: 130, clientY: 120 });
    firePointer(titleBar, 'pointerup', { pointerId: 1, clientX: 142, clientY: 130 });

    await waitFor(() => {
      expect(commitMock).toHaveBeenCalledTimes(1);
    });
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({
        placement: expect.objectContaining({ mode: 'floating', x: 42, y: 30, width: 320, height: 240, collapsed: false }),
      }),
    );
  });

  it('原地点击（无位移）不提交', () => {
    renderChrome();
    const titleBar = screen.getByTestId('fc-test-title-bar');
    firePointer(titleBar, 'pointerdown', { pointerId: 1, clientX: 100, clientY: 100 });
    firePointer(titleBar, 'pointerup', { pointerId: 1, clientX: 100, clientY: 100 });
    expect(commitMock).not.toHaveBeenCalled();
  });

  it('拖动锚定面板就地转 floating（D4）：以当前渲染位置起步', async () => {
    render(
      <FloatingChrome
        component={{ id: 'comp-1', type: 'chart_panel', enabled: true, position: 'top-left' }}
        title="锚定面板"
        testId="fc-anchor"
      >
        <div>面板内容</div>
      </FloatingChrome>,
    );
    const titleBar = screen.getByTestId('fc-anchor-title-bar');
    firePointer(titleBar, 'pointerdown', { pointerId: 1, clientX: 100, clientY: 100 });
    firePointer(titleBar, 'pointerup', { pointerId: 1, clientX: 145, clientY: 122 });

    await waitFor(() => {
      expect(commitMock).toHaveBeenCalledTimes(1);
    });
    // jsdom 无布局（rect 全零）→ 起点为 0，位移即终点的 x/y
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({
        placement: expect.objectContaining({ mode: 'floating', x: 45, y: 22 }),
      }),
    );
  });
});

describe('FloatingChrome — 缩放手势', () => {
  it('resize 收尾：一次提交新 width/height', async () => {
    renderChrome();
    const handle = screen.getByTestId('fc-test-resize-handle');
    firePointer(handle, 'pointerdown', { pointerId: 2, clientX: 300, clientY: 300 });
    firePointer(handle, 'pointermove', { pointerId: 2, clientX: 360, clientY: 340 });
    firePointer(handle, 'pointerup', { pointerId: 2, clientX: 360, clientY: 340 });

    await waitFor(() => {
      expect(commitMock).toHaveBeenCalledTimes(1);
    });
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({
        placement: expect.objectContaining({ mode: 'floating', x: 0, y: 0, width: 380, height: 280 }),
      }),
    );
  });

  it('resize 钳制最小尺寸 160x120', async () => {
    renderChrome();
    const handle = screen.getByTestId('fc-test-resize-handle');
    firePointer(handle, 'pointerdown', { pointerId: 2, clientX: 300, clientY: 300 });
    firePointer(handle, 'pointerup', { pointerId: 2, clientX: 10, clientY: 10 });

    await waitFor(() => {
      expect(commitMock).toHaveBeenCalledTimes(1);
    });
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({
        placement: expect.objectContaining({ width: 160, height: 120 }),
      }),
    );
  });
});

describe('FloatingChrome — 标题栏操作', () => {
  it('折叠：提交 placement.collapsed=true，内容隐藏', async () => {
    renderChrome();
    const toggle = screen.getByRole('button', { name: '折叠面板' });
    act(() => {
      fireEvent.click(toggle);
    });
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({
        placement: expect.objectContaining({ mode: 'floating', x: 0, y: 0, collapsed: true }),
      }),
    );
    // 乐观 override 生效：折叠后只剩标题栏（内容/缩放手柄退场）
    await waitFor(() => {
      expect(screen.queryByText('面板内容')).toBeNull();
      expect(screen.queryByTestId('fc-test-resize-handle')).toBeNull();
    });
    // 再展开：collapsed=false
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '展开面板' }));
    });
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({
        placement: expect.objectContaining({ collapsed: false }),
      }),
    );
  });

  it('隐藏：提交 enabled=false', () => {
    renderChrome();
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '隐藏面板' }));
    });
    expect(commitMock).toHaveBeenCalledWith('comp-1', { enabled: false });
  });

  it('复位：清 override 并提交 anchor 缺省槽位', () => {
    renderChrome();
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '重置位置' }));
    });
    expect(commitMock).toHaveBeenCalledTimes(1);
    expect(commitMock).toHaveBeenCalledWith(
      'comp-1',
      expect.objectContaining({ placement: { mode: 'anchor', anchor: 'top-left' } }),
    );
  });
});
