import { describe, it, expect, vi } from 'vitest';

// Mock maplibre Map
function createMockMap(layerType: string = 'fill') {
  const paintProps: Record<string, any> = {};
  const layoutProps: Record<string, any> = {};
  return {
    getLayer: () => ({ type: layerType }),
    getPaintProperty: (id: string, prop: string) => paintProps[prop],
    setPaintProperty: vi.fn((id: string, prop: string, value: any) => { paintProps[prop] = value; }),
    setLayoutProperty: vi.fn((id: string, prop: string, value: any) => { layoutProps[prop] = value; }),
  };
}

// We'll import after the mock is ready
import { updateLayerStyle } from './renderer';

describe('updateLayerStyle expanded properties', () => {
  it('sets stroke color on fill layer', () => {
    const map = createMockMap('fill');
    updateLayerStyle(map as any, 'test-layer', {
      color: '#ff0000',
      strokeColor: '#000000',
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-color', '#ff0000');
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'fill-outline-color', '#000000');
  });

  it('sets point size on circle layer', () => {
    const map = createMockMap('circle');
    updateLayerStyle(map as any, 'test-layer', {
      pointSize: 8,
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'circle-radius', 8);
  });

  it('sets dash array on line layer', () => {
    const map = createMockMap('line');
    updateLayerStyle(map as any, 'test-layer', {
      dashArray: 'dashed',
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'line-dasharray', [4, 2]);
  });

  it('sets stroke color on circle layer', () => {
    const map = createMockMap('circle');
    updateLayerStyle(map as any, 'test-layer', {
      strokeColor: '#333333',
    });
    expect(map.setPaintProperty).toHaveBeenCalledWith('test-layer', 'circle-stroke-color', '#333333');
  });
});
