/**
 * SketchEditor（V7 Phase E）—— 保存条与丢弃路径。
 *
 * 地图交互（源/图层挂载、点击绘制、顶点拖拽）依赖完整 maplibre 实例，
 * 归集成/手工验证；此处锁定 store 纪律的组件面：
 * - sketchDirty=false 不渲染保存条；
 * - dirty + 有要素 → 保存条出现；「丢弃」清空要素并复位脏标记。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { getSketchState, replaceSketchFeatures, resetSketchStore } from '@/lib/edit/sketch-store';
import { SketchEditor } from './sketch-editor';
import type { MapRef } from 'react-map-gl/maplibre';

const mapRef = { current: null } as unknown as React.RefObject<MapRef | null>;

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
