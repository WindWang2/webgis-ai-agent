# Advanced Renderer Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a professional layer and style management module in `frontend/lib/map-kit/renderer.ts`.

**Architecture:** A set of utility functions for interacting with MapLibre GL JS `Map` instances, ensuring safe source and layer lifecycle management.

**Tech Stack:** TypeScript, MapLibre GL JS, Vitest.

---

### Task 1: Setup and `addGeoJsonSource`

**Files:**
- Create: `frontend/lib/map-kit/renderer.ts`
- Create: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Write the failing test for `addGeoJsonSource`**

```typescript
// frontend/lib/map-kit/renderer.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { addGeoJsonSource } from './renderer';
import maplibregl from 'maplibre-gl';

describe('renderer', () => {
  let mapMock: any;

  beforeEach(() => {
    mapMock = {
      getSource: vi.fn(),
      addSource: vi.fn(),
      addLayer: vi.fn(),
      getLayer: vi.fn(),
      removeLayer: vi.fn(),
      removeSource: vi.fn(),
      setLayoutProperty: vi.fn(),
      setPaintProperty: vi.fn(),
      getStyle: vi.fn(() => ({ layers: [] })),
    };
  });

  describe('addGeoJsonSource', () => {
    it('should add a new GeoJSON source if it does not exist', () => {
      mapMock.getSource.mockReturnValue(undefined);
      const data = { type: 'FeatureCollection', features: [] };
      addGeoJsonSource(mapMock, 'test-source', data);

      expect(mapMock.addSource).toHaveBeenCalledWith('test-source', {
        type: 'geojson',
        data
      });
    });

    it('should update existing GeoJSON source if it exists', () => {
      const sourceMock = { setData: vi.fn() };
      mapMock.getSource.mockReturnValue(sourceMock);
      const data = { type: 'FeatureCollection', features: [] };
      addGeoJsonSource(mapMock, 'test-source', data);

      expect(sourceMock.setData).toHaveBeenCalledWith(data);
      expect(mapMock.addSource).not.toHaveBeenCalled();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation for `addGeoJsonSource`**

```typescript
// frontend/lib/map-kit/renderer.ts
import maplibregl from 'maplibre-gl';

export function addGeoJsonSource(map: any, id: string, data: any) {
  const source = map.getSource(id) as maplibregl.GeoJSONSource;
  if (source) {
    source.setData(data);
  } else {
    map.addSource(id, {
      type: 'geojson',
      data
    });
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/renderer.ts frontend/lib/map-kit/renderer.test.ts
git commit -m "feat(map-kit): implement addGeoJsonSource"
```

---

### Task 2: Implement `addVectorLayer`

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Write the failing test for `addVectorLayer`**

```typescript
// Add to frontend/lib/map-kit/renderer.test.ts
import { addVectorLayer } from './renderer';

describe('addVectorLayer', () => {
  it('should add a new vector layer', () => {
    mapMock.getLayer.mockReturnValue(undefined);
    addVectorLayer(mapMock, {
      id: 'test-layer',
      source: 'test-source',
      type: 'fill',
      paint: { 'fill-color': '#ff0000' }
    });

    expect(mapMock.addLayer).toHaveBeenCalledWith({
      id: 'test-layer',
      source: 'test-source',
      type: 'fill',
      paint: { 'fill-color': '#ff0000' },
      layout: {}
    }, undefined);
  });

  it('should remove existing layer before adding if it exists', () => {
    mapMock.getLayer.mockReturnValue({});
    addVectorLayer(mapMock, {
      id: 'test-layer',
      source: 'test-source',
      type: 'line'
    });

    expect(mapMock.removeLayer).toHaveBeenCalledWith('test-layer');
    expect(mapMock.addLayer).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: FAIL (addVectorLayer not defined)

- [ ] **Step 3: Write minimal implementation for `addVectorLayer`**

```typescript
// Add to frontend/lib/map-kit/renderer.ts

export interface VectorLayerOptions {
  id: string;
  source: string;
  type: 'circle' | 'line' | 'fill';
  paint?: any;
  layout?: any;
  beforeId?: string;
}

export function addVectorLayer(map: any, options: VectorLayerOptions) {
  if (map.getLayer(options.id)) {
    map.removeLayer(options.id);
  }
  map.addLayer({
    id: options.id,
    type: options.type,
    source: options.source,
    paint: options.paint || {},
    layout: options.layout || {}
  }, options.beforeId);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/renderer.ts frontend/lib/map-kit/renderer.test.ts
git commit -m "feat(map-kit): implement addVectorLayer"
```

---

### Task 3: Implement `addNativeHeatmap`

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Write the failing test for `addNativeHeatmap`**

```typescript
// Add to frontend/lib/map-kit/renderer.test.ts
import { addNativeHeatmap } from './renderer';

describe('addNativeHeatmap', () => {
  it('should add a heatmap layer with classic palette', () => {
    mapMock.getLayer.mockReturnValue(undefined);
    addNativeHeatmap(mapMock, {
      id: 'heatmap-layer',
      source: 'test-source',
      palette: 'classic'
    });

    expect(mapMock.addLayer).toHaveBeenCalledWith(expect.objectContaining({
      id: 'heatmap-layer',
      type: 'heatmap',
      paint: expect.objectContaining({
        'heatmap-color': expect.arrayContaining(['heatmap-density'])
      })
    }), undefined);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: FAIL (addNativeHeatmap not defined)

- [ ] **Step 3: Write minimal implementation for `addNativeHeatmap`**

```typescript
// Add to frontend/lib/map-kit/renderer.ts

export interface HeatmapLayerOptions {
  id: string;
  source: string;
  palette?: 'classic' | 'magma' | 'viridis' | 'thermal';
  radius?: number;
  weight?: number | any[];
  intensity?: number | any[];
  opacity?: number;
  beforeId?: string;
}

const colorRamps = {
  classic: [
    'interpolate', ['linear'], ['heatmap-density'],
    0, 'rgba(0,0,255,0)',
    0.2, 'rgb(0,255,255)',
    0.4, 'rgb(0,255,0)',
    0.6, 'rgb(255,255,0)',
    0.8, 'rgb(255,165,0)',
    1.0, 'rgb(255,0,0)'
  ],
  magma: [
    'interpolate', ['linear'], ['heatmap-density'],
    0, 'rgba(0,0,0,0)',
    0.2, 'rgb(50,10,95)',
    0.5, 'rgb(180,35,115)',
    0.8, 'rgb(250,140,90)',
    1.0, 'rgb(250,240,150)'
  ],
  viridis: [
    'interpolate', ['linear'], ['heatmap-density'],
    0, 'rgba(0,0,0,0)',
    0.25, 'rgb(70,0,85)',
    0.5, 'rgb(35,145,140)',
    0.75, 'rgb(95,200,90)',
    1.0, 'rgb(255,230,35)'
  ],
  thermal: [
    'interpolate', ['linear'], ['heatmap-density'],
    0, 'rgba(0,0,0,0)',
    0.33, 'rgb(0,0,255)',
    0.66, 'rgb(255,255,0)',
    1.0, 'rgb(255,0,0)'
  ]
};

export function addNativeHeatmap(map: any, options: HeatmapLayerOptions) {
  if (map.getLayer(options.id)) {
    map.removeLayer(options.id);
  }
  map.addLayer({
    id: options.id,
    type: 'heatmap',
    source: options.source,
    maxzoom: 18,
    paint: {
      'heatmap-weight': options.weight ?? 1,
      'heatmap-intensity': options.intensity ?? ['interpolate', ['linear'], ['zoom'], 0, 1, 15, 3],
      'heatmap-color': colorRamps[options.palette || 'classic'] || colorRamps.classic,
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 2, 15, options.radius || 30],
      'heatmap-opacity': options.opacity ?? 0.8
    }
  }, options.beforeId);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/renderer.ts frontend/lib/map-kit/renderer.test.ts
git commit -m "feat(map-kit): implement addNativeHeatmap"
```

---

### Task 4: Implement `removeLayerStack`

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Write the failing test for `removeLayerStack`**

```typescript
// Add to frontend/lib/map-kit/renderer.test.ts
import { removeLayerStack } from './renderer';

describe('removeLayerStack', () => {
  it('should remove layer and source if they exist', () => {
    mapMock.getLayer.mockReturnValue({});
    mapMock.getSource.mockReturnValue({});
    
    removeLayerStack(mapMock, 'test-id');

    expect(mapMock.removeLayer).toHaveBeenCalledWith('test-id');
    expect(mapMock.removeSource).toHaveBeenCalledWith('test-id');
  });

  it('should not throw if layer or source does not exist', () => {
    mapMock.getLayer.mockReturnValue(undefined);
    mapMock.getSource.mockReturnValue(undefined);
    
    expect(() => removeLayerStack(mapMock, 'test-id')).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: FAIL (removeLayerStack not defined)

- [ ] **Step 3: Write minimal implementation for `removeLayerStack`**

```typescript
// Add to frontend/lib/map-kit/renderer.ts

export function removeLayerStack(map: any, id: string) {
  if (map.getLayer(id)) {
    map.removeLayer(id);
  }
  if (map.getSource(id)) {
    map.removeSource(id);
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/renderer.ts frontend/lib/map-kit/renderer.test.ts
git commit -m "feat(map-kit): implement removeLayerStack"
```

---

### Task 5: Implement `updateLayerStyle`

**Files:**
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Write the failing test for `updateLayerStyle`**

```typescript
// Add to frontend/lib/map-kit/renderer.test.ts
import { updateLayerStyle } from './renderer';

describe('updateLayerStyle', () => {
  it('should update visibility', () => {
    mapMock.getLayer.mockReturnValue({ type: 'fill' });
    updateLayerStyle(mapMock, 'test-layer', { visible: false });

    expect(mapMock.setLayoutProperty).toHaveBeenCalledWith('test-layer', 'visibility', 'none');
  });

  it('should update opacity based on layer type', () => {
    mapMock.getLayer.mockReturnValue({ type: 'line' });
    updateLayerStyle(mapMock, 'test-layer', { opacity: 0.5 });

    expect(mapMock.setPaintProperty).toHaveBeenCalledWith('test-layer', 'line-opacity', 0.5);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: FAIL (updateLayerStyle not defined)

- [ ] **Step 3: Write minimal implementation for `updateLayerStyle`**

```typescript
// Add to frontend/lib/map-kit/renderer.ts

export interface LayerStyleUpdate {
  visible?: boolean;
  opacity?: number;
}

export function updateLayerStyle(map: any, id: string, style: LayerStyleUpdate) {
  const layer = map.getLayer(id);
  if (!layer) return;

  if (style.visible !== undefined) {
    map.setLayoutProperty(id, 'visibility', style.visible ? 'visible' : 'none');
  }

  if (style.opacity !== undefined) {
    let prop = '';
    switch (layer.type) {
      case 'fill': prop = 'fill-opacity'; break;
      case 'line': prop = 'line-opacity'; break;
      case 'circle': prop = 'circle-opacity'; break;
      case 'heatmap': prop = 'heatmap-opacity'; break;
      case 'raster': prop = 'raster-opacity'; break;
    }
    if (prop) {
      map.setPaintProperty(id, prop, style.opacity);
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/renderer.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/renderer.ts frontend/lib/map-kit/renderer.test.ts
git commit -m "feat(map-kit): implement updateLayerStyle"
```
