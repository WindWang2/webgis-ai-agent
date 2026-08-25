import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useSyncExternalStore } from 'react';
import type { Layer } from '@/lib/types/layer';

/**
 * FE-03: the opacity `<input type="range">` used to call `updateLayer` on every
 * onChange tick. Each tick rebuilt the whole layers array → the map-panel
 * reconcile effect fired on every drag step → worker spin-up + clone + layer
 * re-add. The fix debounces the store write: onChange updates only local slider
 * state; the store is updated once on commit (onPointerUp / onBlur).
 *
 * These tests pin that contract: dragging the slider must NOT call updateLayer
 * per tick; committing (pointer up / blur) calls it exactly once with the final
 * value.
 */

const updateLayer = vi.fn();
const toggleLayer = vi.fn();
const removeLayer = vi.fn();
const reorderLayers = vi.fn();
const focusLayer = vi.fn();

// A mutable store the component reads from. Tests swap `layers` per case via
// setStoreLayers(); the selector returns whatever the current store holds.
const store: Record<string, unknown> = {
  layers: [] as Layer[],
  toggleLayer,
  removeLayer,
  updateLayer,
  reorderLayers,
  focusLayer,
  theme: 'dark',
};

function setStoreLayers(layers: Layer[]) {
  store.layers = layers;
}

const mutationMocks = vi.hoisted(() => ({
  setLayerOpacityAndCommit: vi.fn(),
  toggleLayerAndCommit: vi.fn(),
  commitLayerPresentation: vi.fn(),
  removeLayerAndCommit: vi.fn(),
  reorderLayersAndCommit: vi.fn(),
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (selector: (s: any) => any) => selector(store),
    { getState: () => store },
  ),
}));

vi.mock('@/lib/mapspec/user-mutation', () => mutationMocks);

// Import AFTER the mock is registered so the component picks up the mock.
import { LayersTab } from './layers-tab';

function makeLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'L1',
    name: 'Layer One',
    type: 'vector',
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

describe('LayersTab — opacity slider debounce (FE-03)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setStoreLayers([]);
  });

  it('does NOT call updateLayer on every onChange drag tick', () => {
    setStoreLayers([makeLayer({ opacity: 1 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider).toBeInTheDocument();

    // Simulate a drag: several onChange ticks (100 → 80 → 60 → 40).
    fireEvent.change(slider, { target: { value: '100' } });
    fireEvent.change(slider, { target: { value: '80' } });
    fireEvent.change(slider, { target: { value: '60' } });
    fireEvent.change(slider, { target: { value: '40' } });

    // The store write must NOT happen per tick.
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('commits the final opacity to the store once on pointer up', () => {
    setStoreLayers([makeLayer({ opacity: 1 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;

    // Drag through several ticks...
    fireEvent.change(slider, { target: { value: '90' } });
    fireEvent.change(slider, { target: { value: '70' } });
    fireEvent.change(slider, { target: { value: '50' } });
    // ...then release the pointer (commit).
    fireEvent.pointerUp(slider);

    // Exactly one store write, carrying the final value (50% = 0.5).
    expect(updateLayer).toHaveBeenCalledTimes(1);
    expect(updateLayer).toHaveBeenCalledWith('L1', { opacity: 0.5 });
    expect(mutationMocks.setLayerOpacityAndCommit).toHaveBeenCalledWith('L1', 0.5);
  });

  it('commits on blur as well (keyboard / focus-loss commit path)', () => {
    setStoreLayers([makeLayer({ opacity: 1 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '30' } });
    fireEvent.blur(slider);

    expect(updateLayer).toHaveBeenCalledTimes(1);
    expect(updateLayer).toHaveBeenCalledWith('L1', { opacity: 0.3 });
    expect(mutationMocks.setLayerOpacityAndCommit).toHaveBeenCalledWith('L1', 0.3);
  });

  it('does not commit when the value did not change during a drag', () => {
    setStoreLayers([makeLayer({ opacity: 0.5 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    // Slider starts at 50%; user grabs + releases without moving.
    fireEvent.pointerUp(slider);

    expect(updateLayer).not.toHaveBeenCalled();
  });
});

describe('LayersTab — commitOpacity 不在渲染阶段写 store', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setStoreLayers([]);
  });

  /**
   * 2026-08-25 会话回归：commitOpacity 曾把 updateLayer（zustand set →
   * EmbodiedHud 的 setState）放进 setOpacityDraft 的 updater 里。React 在
   * 渲染阶段执行 updater（要求纯函数），于是浏览器报 "Cannot update a
   * component (EmbodiedHud) while rendering a different component
   * (LayersTab)"。这里用一个 useSyncExternalStore 订阅者扮演 EmbodiedHud：
   * 若 updateLayer 仍在渲染阶段被调用，React 会通过 console.error 发出
   * 该警告，断言捕获不到它。
   */
  it('提交透明度不触发 setState-in-render 警告', () => {
    const listeners = new Set<() => void>();
    let version = 0;
    const notify = () => {
      version += 1;
      listeners.forEach((l) => l());
    };
    updateLayer.mockImplementation(() => notify());

    // 订阅者组件：等同 EmbodiedHud 对 store 的 useSyncExternalStore 订阅
    function StoreSubscriber() {
      useSyncExternalStore(
        (cb) => {
          listeners.add(cb);
          return () => listeners.delete(cb);
        },
        () => version
      );
      return null;
    }

    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      setStoreLayers([makeLayer({ opacity: 1 })]);
      render(
        <div>
          <StoreSubscriber />
          <LayersTab />
        </div>
      );

      const slider = screen.getByRole('slider');
      fireEvent.change(slider, { target: { value: '50' } });
      act(() => {
        fireEvent.pointerUp(slider);
      });
      // 重复提交路径（pointerUp 之后紧跟 blur）也应干净
      act(() => {
        fireEvent.blur(slider);
      });

      const renderPhaseWrites = errSpy.mock.calls
        .map((c) => c.map(String).join(' '))
        .filter((t) => t.includes('Cannot update a component'));
      expect(renderPhaseWrites).toEqual([]);
      // 提交本身仍然发生（修复不能以丢提交为代价）
      expect(updateLayer).toHaveBeenCalledWith('L1', { opacity: 0.5 });
    } finally {
      errSpy.mockRestore();
      updateLayer.mockImplementation(undefined);
    }
  });
});

describe('LayersTab — 缩放到图层', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setStoreLayers([]);
  });

  it('点击「缩放到图层」触发 focusLayer(图层 id)', () => {
    setStoreLayers([makeLayer({ id: 'poi', name: '成都小学' })]);
    render(<LayersTab />);

    fireEvent.click(screen.getByRole('button', { name: '缩放到图层 成都小学' }));
    expect(focusLayer).toHaveBeenCalledTimes(1);
    expect(focusLayer).toHaveBeenCalledWith('poi');
  });

  it('每个图层行都有独立的缩放按钮', () => {
    setStoreLayers([
      makeLayer({ id: 'a', name: '图层A' }),
      makeLayer({ id: 'b', name: '图层B' }),
    ]);
    render(<LayersTab />);

    fireEvent.click(screen.getByRole('button', { name: '缩放到图层 图层B' }));
    expect(focusLayer).toHaveBeenCalledWith('b');
  });
});

describe('LayersTab — visibility commits with rollback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setStoreLayers([]);
  });

  it('hides through toggleLayerAndCommit so a 409 can roll HUD back', () => {
    setStoreLayers([makeLayer({ id: 'L1', name: 'Schools', visible: true })]);
    render(<LayersTab />);

    fireEvent.click(screen.getByLabelText('隐藏图层'));

    expect(mutationMocks.toggleLayerAndCommit).toHaveBeenCalledTimes(1);
    expect(mutationMocks.toggleLayerAndCommit).toHaveBeenCalledWith('L1');
    expect(toggleLayer).not.toHaveBeenCalled();
  });
});
