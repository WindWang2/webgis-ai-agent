/**
 * G16：label-editor 组件旅程（工具门控/选中/隐藏提交/文字覆写/reset 语义）。
 * House style：jsdom + stub map ref（sketch-editor.test 同款轻量）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { LabelEditor } from './label-editor';
import { useHudStore } from '@/lib/store/useHudStore';

// —— commit 通道 mock（断言一发一笔、原子 ops 形状）——
const commitLabelAuthoring = vi.fn(async () => {});
vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitLabelAuthoring: (...args: unknown[]) => commitLabelAuthoring(...args),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => ({
    layers: [{
      id: 'schools',
      label: {
        field: 'name', size: 12,
        authoring: { overrides: [{ featureId: 's1', pinned: true, offset: { dx: 1, dy: 2 } }] },
      },
    }],
  }),
}));
vi.mock('@/lib/workbench/undo', () => ({
  recordCommand: vi.fn(() => 'cmd-1'),
}));

function makeMapRef() {
  const handlers = new Map<string, Set<(...a: never[]) => void>>();
  const map: any = {
    getStyle: () => ({
      layers: [
        { id: 'schools-label' },
        { id: 'schools-label-pinned' },
        { id: 'baselayer' },
      ],
    }),
    queryRenderedFeatures: (_pt: unknown, opts: { layers?: string[] }) => {
      const want = opts?.layers ?? [];
      if (want.includes('schools-label')) {
        return [{
          layer: { id: 'schools-label' },
          id: 's1',
          properties: { name: '第一小学' },
        }];
      }
      return [];
    },
    // MapLibre 委托签名：on(type, layerIds, handler) / on(type, handler)。
    on: (ev: string, arg2: unknown, maybeHandler?: (...a: never[]) => void) => {
      const h = (maybeHandler ?? arg2) as (...a: never[]) => void;
      if (typeof h !== 'function') return;
      const s = handlers.get(ev) ?? new Set();
      s.add(h);
      handlers.set(ev, s);
    },
    off: (ev: string, arg2: unknown, maybeHandler?: (...a: never[]) => void) => {
      const h = (maybeHandler ?? arg2) as (...a: never[]) => void;
      if (typeof h !== 'function') return;
      handlers.get(ev)?.delete(h);
    },
  };
  return { map, handlers, ref: { current: map } as never };
}

describe('LabelEditor（G16 UI）', () => {
  beforeEach(() => {
    commitLabelAuthoring.mockClear();
    useHudStore.getState().setActiveMapTool(null);
  });

  it('工具未激活：零渲染（不抢占）', () => {
    const { ref } = makeMapRef();
    const { container } = render(<LabelEditor mapRef={ref} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('工具激活 → 面板出现；点击标签选中目标（读 spec 覆写）', async () => {
    const { ref, handlers } = makeMapRef();
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    expect(screen.getByRole('group')).toBeTruthy();
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    expect(down).toBeTruthy();
    act(() => {
      down({ point: { x: 10, y: 10 }, lngLat: { lng: 116, lat: 39 } });
    });
    // 选中 s1（spec 中已有 pinned 覆写 → 按钮可见且文字草稿无值）。
    expect(await screen.findByText(/s1/)).toBeTruthy();
  });

  it('隐藏 → 一笔原子 set（featureId + hidden）', async () => {
    const { ref, handlers } = makeMapRef();
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 10, y: 10 }, lngLat: { lng: 116, lat: 39 } }));
    const hideBtn = await screen.findByTestId('label-editor-hide');
    await act(async () => {
      fireEvent.click(hideBtn);
    });
    expect(commitLabelAuthoring).toHaveBeenCalledTimes(1);
    const [layerId, ops] = commitLabelAuthoring.mock.calls[0];
    expect(layerId).toBe('schools');
    expect(ops).toEqual([
      { featureId: 's1', op: 'set', set: { hidden: true } },
    ]);
  });

  it('文字覆写输入 + Enter → set.text（修改标签文字 ≠ 修改数据）', async () => {
    const { ref, handlers } = makeMapRef();
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 10, y: 10 }, lngLat: { lng: 116, lat: 39 } }));
    const input = await screen.findByTestId('label-editor-text-input');
    await act(async () => {
      fireEvent.change(input, { target: { value: '市重点一小' } });
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(commitLabelAuthoring).toHaveBeenCalledWith('schools', [
      { featureId: 's1', op: 'set', set: { text: '市重点一小' } },
    ]);
  });

  it('reset 语义按作用域发 op（position/text/all）', async () => {
    const { ref, handlers } = makeMapRef();
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 10, y: 10 }, lngLat: { lng: 116, lat: 39 } }));
    const byTestid = (id: string) => screen.getByTestId(id);
    await act(async () => {
      fireEvent.click(byTestid('label-editor-reset-position'));
    });
    expect(commitLabelAuthoring).toHaveBeenLastCalledWith('schools', [
      { featureId: 's1', op: 'reset', scope: 'position' },
    ]);
    await act(async () => {
      fireEvent.click(byTestid('label-editor-reset-text'));
    });
    expect(commitLabelAuthoring).toHaveBeenLastCalledWith('schools', [
      { featureId: 's1', op: 'reset', scope: 'text' },
    ]);
    await act(async () => {
      fireEvent.click(byTestid('label-editor-reset-all'));
    });
    expect(commitLabelAuthoring).toHaveBeenLastCalledWith('schools', [
      { featureId: 's1', op: 'reset', scope: 'all' },
    ]);
  });

  it('拖动只提交一笔（pointerup 提交；move 不发服务端）', async () => {
    const { ref, handlers } = makeMapRef();
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    const move = Array.from(handlers.get('mousemove') ?? [])[0] as never as (e: unknown) => void;
    const up = Array.from(handlers.get('mouseup') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 0, y: 0 }, lngLat: { lng: 116, lat: 39 } }));
    // 一串 move（本地预览 —— 零服务端调用）。
    for (let i = 1; i <= 20; i += 1) {
      act(() => move({ point: { x: i * 3, y: i * 2 } }));
    }
    expect(commitLabelAuthoring).not.toHaveBeenCalled();
    await act(async () => {
      up({});
    });
    expect(commitLabelAuthoring).toHaveBeenCalledTimes(1);
    const [, ops] = commitLabelAuthoring.mock.calls[0];
    expect(ops[0].set.pinned).toBe(true);
    // px→em：+60px/12px 字号 = 5em；+40px → 10/3em；叠加既有 (1,2) →
    // (6, 5.33)（round2 精度）。
    expect(ops[0].set.offset).toEqual({ dx: 6, dy: 5.33 });
  });
});

// ── R1 复审回归 ─────────────────────────────────────────────────────────
describe('LabelEditor R1 review fixes', () => {
  beforeEach(() => {
    commitLabelAuthoring.mockClear();
    useHudStore.getState().setActiveMapTool(null);
  });

  it('P2-10：拖动中 Esc → mouseup 零提交（取消纪律）', async () => {
    const { ref, handlers } = makeMapRef();
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    const move = Array.from(handlers.get('mousemove') ?? [])[0] as never as (e: unknown) => void;
    const up = Array.from(handlers.get('mouseup') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 0, y: 0 }, lngLat: { lng: 116, lat: 39 } }));
    act(() => move({ point: { x: 30, y: 20 } }));
    // Escape 栈：keydown 派发（capture）→ 取消标记。
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    await act(async () => {
      up({});
    });
    expect(commitLabelAuthoring).not.toHaveBeenCalled();
  });

  it('P2-11：键盘微调 Enter 不双计 —— 真实 bug 路径（无已提交 offset）', async () => {
    // 旧缺陷只在目标**没有**已提交 offset 时触发（预览落入 pendingOffset
    // 被当基准再加）。让 mock 返回 s2（spec 覆写只有 s1）。
    const { ref, handlers, map } = makeMapRef();
    map.queryRenderedFeatures = () => [{
      layer: { id: 'schools-label' },
      id: 's2',
      properties: { name: '第二小学' },
    }];
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 0, y: 0 }, lngLat: { lng: 116, lat: 39 } }));
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    expect(commitLabelAuthoring).toHaveBeenCalledTimes(1);
    const [, ops] = commitLabelAuthoring.mock.calls[0];
    // 无既有 offset：Enter = 0.25（而非 0.25+0.25=0.5 双计）。
    expect(ops[0].featureId).toBe('s2');
    expect(ops[0].set.offset).toEqual({ dx: 0.25, dy: 0 });
  });

  it('P2-11b：连续方向键跨 effect 重挂累计（nudge ref 存活）', async () => {
    const { ref, handlers, map } = makeMapRef();
    map.queryRenderedFeatures = () => [{
      layer: { id: 'schools-label' },
      id: 's2',
      properties: { name: '第二小学' },
    }];
    act(() => useHudStore.getState().setActiveMapTool('edit_labels'));
    render(<LabelEditor mapRef={ref} />);
    const down = Array.from(handlers.get('mousedown') ?? [])[0] as never as (e: unknown) => void;
    act(() => down({ point: { x: 0, y: 0 }, lngLat: { lng: 116, lat: 39 } }));
    // 三次 ArrowRight（每次 setTarget → effect 重挂 —— 旧实现闭包清零）。
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      });
    }
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    const [, ops] = commitLabelAuthoring.mock.calls[0];
    expect(ops[0].set.offset).toEqual({ dx: 0.75, dy: 0 });
  });
});
