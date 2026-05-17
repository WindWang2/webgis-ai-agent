# Map-Kit Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the implementation of the map-kit renderer utility and its tests.

**Architecture:** A set of pure-ish utility functions that interact with a MapLibre GL instance to manage layers and sources.

**Tech Stack:** TypeScript, MapLibre GL, Vitest.

---

### Task 1: Implement addVectorLayer

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Test: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Write the minimal implementation in renderer.ts**

```typescript
export interface VectorLayerOptions {
  id: string;
  source: string;
  type: 'circle' | 'line' | 'fill';
  paint?: any;
  layout?: any;
  minzoom?: number;
  maxzoom?: number;
  filter?: any[];
}

export function addVectorLayer(map: any, options: VectorLayerOptions, beforeId?: string) {
  if (map.getLayer(options.id)) {
    map.removeLayer(options.id);
  }

  map.addLayer({
    id: options.id,
    type: options.type,
    source: options.source,
    paint: options.paint || {},
    layout: options.layout || {},
    ...(options.minzoom !== undefined && { minzoom: options.minzoom }),
    ...(options.maxzoom !== undefined && { maxzoom: options.maxzoom }),
    ...(options.filter && { filter: options.filter }),
  }, beforeId);
}
```

- [ ] **Step 2: Run existing tests to verify they pass**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/map-kit/renderer.ts
git commit -m "feat: implement addVectorLayer"
```

### Task 2: Implement addNativeHeatmap

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Define palettes and implement addNativeHeatmap**

```typescript
const HEATMAP_PALETTES = {
  classic: [
    0, 'rgba(33,102,172,0)',
    0.2, 'rgb(103,169,207)',
    0.4, 'rgb(209,229,240)',
    0.6, 'rgb(253,219,199)',
    0.8, 'rgb(239,138,98)',
    1, 'rgb(178,24,43)'
  ],
  magma: [
    0, 'rgba(0,0,4,0)',
    0.2, 'rgb(81,18,124)',
    0.4, 'rgb(182,54,121)',
    0.6, 'rgb(251,136,97)',
    0.8, 'rgb(252,253,191)',
    1, 'rgb(255,255,255)'
  ],
  viridis: [
    0, 'rgba(68,1,84,0)',
    0.2, 'rgb(59,82,139)',
    0.4, 'rgb(33,145,140)',
    0.6, 'rgb(94,201,98)',
    0.8, 'rgb(253,231,37)',
    1, 'rgb(255,255,255)'
  ],
  thermal: [
    0, 'rgba(0,0,255,0)',
    0.2, 'rgb(0,255,255)',
    0.4, 'rgb(0,255,0)',
    0.6, 'rgb(255,255,0)',
    0.8, 'rgb(255,0,0)',
    1, 'rgb(255,255,255)'
  ]
};

export interface HeatmapOptions {
  id: string;
  source: string;
  palette?: keyof typeof HEATMAP_PALETTES;
  radius?: number;
  weight?: any;
  intensity?: number;
  opacity?: number;
}

export function addNativeHeatmap(map: any, options: HeatmapOptions) {
  if (map.getLayer(options.id)) {
    map.removeLayer(options.id);
  }

  const palette = HEATMAP_PALETTES[options.palette || 'classic'];

  map.addLayer({
    id: options.id,
    type: 'heatmap',
    source: options.source,
    paint: {
      'heatmap-weight': options.weight || 1,
      'heatmap-intensity': options.intensity || 1,
      'heatmap-color': [
        'interpolate',
        ['linear'],
        ['heatmap-density'],
        ...palette
      ],
      'heatmap-radius': options.radius || 30,
      'heatmap-opacity': options.opacity || 1
    }
  });
}
```

- [ ] **Step 2: Add test for addNativeHeatmap in renderer.test.ts**

```typescript
  describe('addNativeHeatmap', () => {
    it('should add a heatmap layer with default palette', () => {
      mapMock.getLayer.mockReturnValue(undefined);
      addNativeHeatmap(mapMock, {
        id: 'heatmap-layer',
        source: 'test-source'
      });

      expect(mapMock.addLayer).toHaveBeenCalled();
      const layerArg = mapMock.addLayer.mock.calls[0][0];
      expect(layerArg.type).toBe('heatmap');
      expect(layerArg.paint['heatmap-color']).toBeDefined();
    });

    it('should use specified palette', () => {
      addNativeHeatmap(mapMock, {
        id: 'heatmap-layer',
        source: 'test-source',
        palette: 'viridis'
      });
      const layerArg = mapMock.addLayer.mock.calls[1][0];
      // Viridis starts with rgb(68,1,84)
      expect(JSON.stringify(layerArg.paint['heatmap-color'])).toContain('68,1,84');
    });
  });
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

### Task 3: Implement removeLayerStack

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Implement removeLayerStack**

```typescript
export function removeLayerStack(map: any, id: string) {
  if (map.getLayer(id)) {
    map.removeLayer(id);
  }
  if (map.getSource(id)) {
    map.removeSource(id);
  }
}
```

- [ ] **Step 2: Add test for removeLayerStack**

```typescript
  describe('removeLayerStack', () => {
    it('should remove both layer and source', () => {
      mapMock.getLayer.mockReturnValue({});
      mapMock.getSource.mockReturnValue({});
      
      removeLayerStack(mapMock, 'test-id');
      
      expect(mapMock.removeLayer).toHaveBeenCalledWith('test-id');
      expect(mapMock.removeSource).toHaveBeenCalledWith('test-id');
    });
  });
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

### Task 4: Implement updateLayerStyle

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Implement updateLayerStyle**

```typescript
export function updateLayerStyle(map: any, id: string, style: { visibility?: 'visible' | 'none', opacity?: number }) {
  if (!map.getLayer(id)) return;

  if (style.visibility) {
    map.setLayoutProperty(id, 'visibility', style.visibility);
  }

  if (style.opacity !== undefined) {
    const layer = map.getLayer(id);
    let opacityProp = '';
    switch (layer.type) {
      case 'fill': opacityProp = 'fill-opacity'; break;
      case 'line': opacityProp = 'line-opacity'; break;
      case 'circle': opacityProp = 'circle-opacity'; break;
      case 'heatmap': opacityProp = 'heatmap-opacity'; break;
      case 'raster': opacityProp = 'raster-opacity'; break;
      case 'symbol': opacityProp = 'icon-opacity'; break; // Could also be text-opacity
    }
    if (opacityProp) {
      map.setPaintProperty(id, opacityProp, style.opacity);
    }
  }
}
```

- [ ] **Step 2: Add test for updateLayerStyle**

```typescript
  describe('updateLayerStyle', () => {
    it('should update visibility', () => {
      mapMock.getLayer.mockReturnValue({ type: 'fill' });
      updateLayerStyle(mapMock, 'test-layer', { visibility: 'none' });
      expect(mapMock.setLayoutProperty).toHaveBeenCalledWith('test-layer', 'visibility', 'none');
    });

    it('should update opacity based on layer type', () => {
      mapMock.getLayer.mockReturnValue({ type: 'circle' });
      updateLayerStyle(mapMock, 'test-layer', { opacity: 0.5 });
      expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'circle-opacity', 0.5);
    });
  });
```

- [ ] **Step 3: Run all tests and ensure they pass**

- [ ] **Step 4: Commit and finalize**
