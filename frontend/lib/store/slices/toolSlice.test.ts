import { describe, expect, it, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { MAP_TOOLS, type MapToolId } from './toolSlice';

/**
 * ToolSlice（V7）—— 全局地图工具唯一真相。
 * 契约：
 * - activeMapTool 单字段天然互斥（激活新工具即替换旧工具）；
 * - 再激活同一工具 = 取消（toggle 语义由调用方实现，store 只存真相）；
 * - clearToolState 复位激活态与脏标记，但保留吸附偏好。
 */
describe('toolSlice', () => {
  beforeEach(() => {
    useHudStore.getState().clearToolState();
  });

  // G14 and G16 extend the same closed union; merging either must retain both.
  const mergedTools: MapToolId[] = ['edit_labels', 'split_feature'];
  it.each(mergedTools)('retains %s in the shared tool vocabulary and state', (tool) => {
    expect(MAP_TOOLS).toContain(tool);
    useHudStore.getState().setActiveMapTool(tool);
    expect(useHudStore.getState().activeMapTool).toBe(tool);
    useHudStore.getState().clearToolState();
    expect(useHudStore.getState().activeMapTool).toBeNull();
  });

  it('activating a tool replaces the previous one (mutual exclusion)', () => {
    useHudStore.getState().setActiveMapTool('measure_distance');
    expect(useHudStore.getState().activeMapTool).toBe('measure_distance');
    useHudStore.getState().setActiveMapTool('draw_polygon');
    expect(useHudStore.getState().activeMapTool).toBe('draw_polygon');
  });

  it('deactivates with null', () => {
    useHudStore.getState().setActiveMapTool('brush_select');
    useHudStore.getState().setActiveMapTool(null);
    expect(useHudStore.getState().activeMapTool).toBeNull();
  });

  it('clearToolState resets tool and dirty flag but keeps snapping preference', () => {
    useHudStore.getState().setActiveMapTool('edit_vertices');
    useHudStore.getState().setSketchDirty(true);
    const before = useHudStore.getState().snappingEnabled;
    useHudStore.getState().toggleSnapping();
    expect(useHudStore.getState().snappingEnabled).toBe(!before);
    useHudStore.getState().clearToolState();
    expect(useHudStore.getState().activeMapTool).toBeNull();
    expect(useHudStore.getState().sketchDirty).toBe(false);
    expect(useHudStore.getState().snappingEnabled).toBe(!before);
  });
});
