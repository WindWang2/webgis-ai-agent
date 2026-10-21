/**
 * SketchEditor（V7 Phase E）—— 保存条与丢弃路径。
 *
 * 地图交互（源/图层挂载、点击绘制、顶点拖拽）依赖完整 maplibre 实例，
 * 归集成/手工验证；此处锁定 store 纪律的组件面：
 * - sketchDirty=false 不渲染保存条；
 * - dirty + 有要素 → 保存条出现；「丢弃」清空要素并复位脏标记；
 * - #1746-6：顶点拖拽 effect 的 cleanup 必须恢复全部三个手势（dragPan /
 *   touchZoomRotate / touchPitch），拖拽中途切工具或卸载后触屏双指旋转与
 *   俯仰不得残留失效。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { getSketchState, replaceSketchFeatures, resetSketchStore } from '@/lib/edit/sketch-store';
import { SketchEditor } from './sketch-editor';
import type { MapRef } from 'react-map-gl/maplibre';

const mapRef = { current: null } as unknown as React.RefObject<MapRef | null>;

function makeFakeMap() {
  const sources = new Set<string>();
  const layers = new Set<string>();
  const gestures = {
    dragPan: { enable: vi.fn(), disable: vi.fn() },
    touchZoomRotate: { enableRotation: vi.fn(), disableRotation: vi.fn() },
    touchPitch: { enable: vi.fn(), disable: vi.fn() },
  };
  const map = {
    ...gestures,
    on: vi.fn(),
    off: vi.fn(),
    getSource: vi.fn((id: string) => (sources.has(id) ? { setData: vi.fn() } : undefined)),
    addSource: vi.fn((id: string) => {
      sources.add(id);
    }),
    getLayer: vi.fn((id: string) => (layers.has(id) ? {} : undefined)),
    addLayer: vi.fn((spec: { id: string }) => {
      layers.add(spec.id);
    }),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),
    getCanvas: vi.fn(() => ({ style: {} })),
    project: vi.fn(() => ({ x: 0, y: 0 })),
    unproject: vi.fn(() => ({ lng: 0, lat: 0 })),
    easeTo: vi.fn(),
  };
  return { map, gestures };
}

describe('SketchEditor save bar', () => {
  beforeEach(() => {
    cleanup();
    resetSketchStore();
    useHudStore.getState().clearToolState();
  });

  it('renders nothing when the sketch is clean', () => {
    const { container } = render(<SketchEditor mapRef={mapRef} />);
    expect(container.querySelector('[data-testid="sketch-save-bar"]')).toBeNull();
  });

  it('discard clears features and the dirty flag', () => {
    act(() => {
      useHudStore.getState().setSketchDirty(true);
      replaceSketchFeatures([{
        id: 's1',
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [0, 0] },
        properties: { kind: 'sketch_point' },
      }]);
    });
    render(<SketchEditor mapRef={mapRef} />);
    expect(screen.getByTestId('sketch-save-bar')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '丢弃' }));
    expect(useHudStore.getState().sketchDirty).toBe(false);
    expect(getSketchState().features).toHaveLength(0);
  });
});

describe('SketchEditor 顶点拖拽 cleanup 恢复手势（#1746-6）', () => {
  beforeEach(() => {
    cleanup();
    resetSketchStore();
    useHudStore.getState().clearToolState();
  });

  it('卸载（切工具/组件销毁）时恢复 dragPan / touchZoomRotate / touchPitch', () => {
    const { map, gestures } = makeFakeMap();
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;

    useHudStore.setState({
      mapLoaded: true,
      activeMapTool: 'edit_vertices',
    } as never);

    const { unmount } = render(<SketchEditor mapRef={mapRef} />);

    // effect 挂载即注册拖拽监听；三个手势的 enable 尚未被调用。
    expect(map.on).toHaveBeenCalledWith('mousedown', 'wb-sketch-vertex', expect.any(Function));
    expect(gestures.dragPan.enable).not.toHaveBeenCalled();
    expect(gestures.touchZoomRotate.enableRotation).not.toHaveBeenCalled();
    expect(gestures.touchPitch.enable).not.toHaveBeenCalled();

    unmount();

    expect(gestures.dragPan.enable).toHaveBeenCalledTimes(1);
    expect(gestures.touchZoomRotate.enableRotation).toHaveBeenCalledTimes(1);
    expect(gestures.touchPitch.enable).toHaveBeenCalledTimes(1);
  });
});
