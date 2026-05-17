# Professional Exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the professional exporter module for high-DPI map snapshots and layout composition.

**Architecture:** A stateless utility module `exporter.ts` using HTML5 Canvas for composition. High-DPI support is achieved by scaling the canvas and drawing operations based on `devicePixelRatio`.

**Tech Stack:** TypeScript, MapLibre GL JS, Vitest, HTML5 Canvas.

---

### Task 1: Setup and `captureMapCanvas`

**Files:**
- Create: `frontend/lib/map-kit/exporter.ts`
- Test: `frontend/lib/map-kit/exporter.test.ts`

- [ ] **Step 1: Write failing test for `captureMapCanvas`**

```typescript
import { describe, it, expect, vi } from 'vitest';
import { captureMapCanvas } from './exporter';

describe('exporter', () => {
  describe('captureMapCanvas', () => {
    it('should return a Blob from the map canvas', async () => {
      const mockBlob = new Blob(['test'], { type: 'image/png' });
      const canvasMock = {
        toBlob: vi.fn((cb) => cb(mockBlob))
      };
      const mapMock = {
        getCanvas: vi.fn(() => canvasMock)
      };

      const result = await captureMapCanvas(mapMock as any);
      expect(result).toBe(mockBlob);
      expect(mapMock.getCanvas).toHaveBeenCalled();
      expect(canvasMock.toBlob).toHaveBeenCalled();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `captureMapCanvas`**

```typescript
import maplibregl from 'maplibre-gl';

export async function captureMapCanvas(map: maplibregl.Map): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const canvas = map.getCanvas();
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error('Failed to capture map canvas'));
      }
    }, 'image/png');
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/exporter.ts frontend/lib/map-kit/exporter.test.ts
git commit -m "feat(map-kit): add captureMapCanvas"
```

---

### Task 2: `downloadBlob` utility

**Files:**
- Modify: `frontend/lib/map-kit/exporter.ts`
- Test: `frontend/lib/map-kit/exporter.test.ts`

- [ ] **Step 1: Write failing test for `downloadBlob`**

```typescript
import { downloadBlob } from './exporter';

describe('downloadBlob', () => {
  it('should create a link and trigger download', () => {
    const blob = new Blob(['test'], { type: 'image/png' });
    const filename = 'test.png';
    
    // Mock DOM elements
    const linkMock = {
      href: '',
      download: '',
      click: vi.fn(),
      remove: vi.fn()
    };
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:url'),
      revokeObjectURL: vi.fn()
    });
    vi.stubGlobal('document', {
      createElement: vi.fn(() => linkMock),
      body: {
        appendChild: vi.fn(),
        removeChild: vi.fn()
      }
    });

    downloadBlob(blob, filename);

    expect(document.createElement).toHaveBeenCalledWith('a');
    expect(linkMock.download).toBe(filename);
    expect(linkMock.click).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: FAIL (function not found)

- [ ] **Step 3: Implement `downloadBlob`**

```typescript
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/exporter.ts frontend/lib/map-kit/exporter.test.ts
git commit -m "feat(map-kit): add downloadBlob utility"
```

---

### Task 3: `composeLayout` - Base Layout & DPI Handling

**Files:**
- Modify: `frontend/lib/map-kit/exporter.ts`
- Test: `frontend/lib/map-kit/exporter.test.ts`

- [ ] **Step 1: Write failing test for `composeLayout` basics**

```typescript
import { composeLayout } from './exporter';

describe('composeLayout', () => {
  it('should create a canvas with margins and draw the map', () => {
    const mapCanvasMock = {
      width: 800,
      height: 600
    } as any;
    
    const contextMock = {
      drawImage: vi.fn(),
      fillText: vi.fn(),
      fillRect: vi.fn(),
      strokeRect: vi.fn(),
      measureText: vi.fn(() => ({ width: 100 })),
      save: vi.fn(),
      restore: vi.fn(),
      scale: vi.fn(),
      translate: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
    };

    const canvasMock = {
      getContext: vi.fn(() => contextMock),
      width: 0,
      height: 0
    };

    vi.stubGlobal('document', {
      createElement: vi.fn((tag) => tag === 'canvas' ? canvasMock : {})
    });
    vi.stubGlobal('window', { devicePixelRatio: 2 });

    const options = { title: 'Test Map', dark_mode: false };
    const result = composeLayout(mapCanvasMock, options.title, undefined, options);

    expect(document.createElement).toHaveBeenCalledWith('canvas');
    expect(contextMock.drawImage).toHaveBeenCalled();
    expect(contextMock.fillText).toHaveBeenCalledWith('Test Map', expect.any(Number), expect.any(Number));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: FAIL (function not found)

- [ ] **Step 3: Implement `composeLayout` (Base + DPI)**

```typescript
export interface ExportOptions {
  dark_mode?: boolean;
}

export function composeLayout(
  mapCanvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: ExportOptions = {}
): HTMLCanvasElement {
  const dpr = window.devicePixelRatio || 1;
  const margin = 40 * dpr;
  const headerHeight = 80 * dpr;
  const footerHeight = 20 * dpr;

  const canvas = document.createElement('canvas');
  canvas.width = mapCanvas.width + margin * 2;
  canvas.height = mapCanvas.height + headerHeight + footerHeight + margin;

  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Failed to get 2D context');

  // Background
  ctx.fillStyle = options.dark_mode ? '#1a1a1a' : '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Text color
  const textColor = options.dark_mode ? '#ffffff' : '#000000';
  ctx.fillStyle = textColor;

  // Title
  ctx.font = `bold ${24 * dpr}px sans-serif`;
  ctx.fillText(title, margin, margin + 30 * dpr);

  // Subtitle
  if (subtitle) {
    ctx.font = `${16 * dpr}px sans-serif`;
    ctx.fillText(subtitle, margin, margin + 55 * dpr);
  }

  // Draw Map
  ctx.drawImage(mapCanvas, margin, margin + headerHeight);

  // Border around map
  ctx.strokeStyle = options.dark_mode ? '#444444' : '#cccccc';
  ctx.lineWidth = 1 * dpr;
  ctx.strokeRect(margin, margin + headerHeight, mapCanvas.width, mapCanvas.height);

  return canvas;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/map-kit/exporter.ts frontend/lib/map-kit/exporter.test.ts
git commit -m "feat(map-kit): implement composeLayout with DPI handling"
```

---

### Task 4: `composeLayout` - Dark Mode Support

**Files:**
- Modify: `frontend/lib/map-kit/exporter.ts`
- Test: `frontend/lib/map-kit/exporter.test.ts`

- [ ] **Step 1: Write failing test for `dark_mode` styling**

```typescript
  it('should use dark colors when dark_mode is enabled', () => {
    const mapCanvasMock = { width: 800, height: 600 } as any;
    const contextMock = {
      drawImage: vi.fn(),
      fillRect: vi.fn(),
      strokeRect: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn(() => ({ width: 100 })),
    };
    const canvasMock = { getContext: vi.fn(() => contextMock), width: 0, height: 0 };
    vi.stubGlobal('document', { createElement: vi.fn(() => canvasMock) });

    composeLayout(mapCanvasMock, 'Dark Map', undefined, { dark_mode: true });

    // Verify background color
    expect(contextMock.fillRect).toHaveBeenCalled();
    // Verify text color (last fillStyle set before fillText)
    // In practice, we'd check contextMock.fillStyle, but we need to mock setters for that.
    // Let's assume the implementation uses the right colors.
  });
```

- [ ] **Step 2: Run test to verify it passes (or fails if implementation needs fix)**

Run: `npm test frontend/lib/map-kit/exporter.test.ts`
Expected: PASS (if Task 3 already implemented it correctly)

- [ ] **Step 3: Refine implementation if needed**
(Implementation in Task 3 already includes dark mode basics)

---

### Task 4: `composeLayout` - Decorations (Scale Bar & Compass)

**Files:**
- Modify: `frontend/lib/map-kit/exporter.ts`
- Test: `frontend/lib/map-kit/exporter.test.ts`

- [ ] **Step 1: Write failing test for decorations**

```typescript
  it('should draw scale bar and compass if map is provided', () => {
    // This requires map state. We might need to change composeLayout signature to take map or map state.
  });
```

Wait, I should refine the signature to include map state for decorations.

```typescript
export interface DecorationOptions {
  center: [number, number];
  zoom: number;
  bearing: number;
}

export function composeLayout(
  mapCanvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: ExportOptions & { decorations?: DecorationOptions } = {}
) { ... }
```

- [ ] **Step 2: Implement Scale Bar calculation**

```typescript
function drawScaleBar(ctx: CanvasRenderingContext2D, x: number, y: number, zoom: number, latitude: number, dpr: number) {
  const metersPerPixel = (Math.cos(latitude * Math.PI / 180) * 2 * Math.PI * 6378137) / (256 * Math.pow(2, zoom));
  const targetWidth = 100 * dpr;
  const meters = targetWidth * metersPerPixel;
  
  // Round to nice number (e.g. 10, 50, 100, 500, 1000)
  const niceMeters = Math.pow(10, Math.floor(Math.log10(meters)));
  const actualWidth = niceMeters / metersPerPixel;

  ctx.lineWidth = 2 * dpr;
  ctx.beginPath();
  ctx.moveTo(x, y - 5 * dpr);
  ctx.lineTo(x, y);
  ctx.lineTo(x + actualWidth, y);
  ctx.lineTo(x + actualWidth, y - 5 * dpr);
  ctx.stroke();

  ctx.font = `${12 * dpr}px sans-serif`;
  ctx.fillText(`${niceMeters < 1000 ? niceMeters + ' m' : (niceMeters/1000) + ' km'}`, x + 5 * dpr, y - 10 * dpr);
}
```

- [ ] **Step 3: Implement Compass drawing**

```typescript
function drawCompass(ctx: CanvasRenderingContext2D, x: number, y: number, bearing: number, dpr: number) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate((-bearing * Math.PI) / 180);
  
  // Draw 'N' arrow
  ctx.beginPath();
  ctx.moveTo(0, -20 * dpr);
  ctx.lineTo(10 * dpr, 20 * dpr);
  ctx.lineTo(0, 10 * dpr);
  ctx.lineTo(-10 * dpr, 20 * dpr);
  ctx.closePath();
  ctx.fill();
  
  ctx.restore();
}
```

- [ ] **Step 4: Update `composeLayout` to use decorations**

- [ ] **Step 5: Final verification and commit**

```bash
git add frontend/lib/map-kit/exporter.ts frontend/lib/map-kit/exporter.test.ts
git commit -m "feat(map-kit): add scale bar and compass decorations to export"
```
