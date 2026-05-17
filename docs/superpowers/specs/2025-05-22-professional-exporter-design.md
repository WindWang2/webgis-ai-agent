# Professional Exporter Module Design

## Goal
Implement a professional high-DPI map snapshot and layout composition module for the map toolkit.

## Architecture
The module will reside in `frontend/lib/map-kit/exporter.ts`. It will use the HTML5 Canvas API for image composition and MapLibre GL JS for map state and canvas capture.

## Components

### 1. `captureMapCanvas(map: maplibregl.Map): Promise<Blob>`
- Captures the current state of the map's canvas.
- Handles the `preserveDrawingBuffer` constraint by ensuring we capture at the right moment if needed, or by using MapLibre's internal methods.
- Returns a `Blob` for further processing or direct download.

### 2. `composeLayout(mapCanvas: HTMLCanvasElement, title: string, subtitle?: string, options: ExportOptions): HTMLCanvasElement`
- **Layout Structure**:
  - Header: Title and Subtitle.
  - Body: Map Canvas.
  - Footer/Overlays: Scale bar, Compass, and Metadata.
- **High-DPI Support**: Uses `window.devicePixelRatio` to scale all drawn elements (text, lines, margins).
- **Dark Mode**: 
  - `dark_mode: true` -> Background: `#1a1a1a`, Text: `white`.
  - `dark_mode: false` -> Background: `white`, Text: `black`.
- **Decorations**:
  - **Scale Bar**: Calculated using `map.getCenter()` and `map.getZoom()`. Draws a metric scale bar.
  - **Compass**: Simple needle/arrow pointing to `map.getBearing()`.

### 3. `downloadBlob(blob: Blob, filename: string)`
- Utility to trigger a browser download for the generated image.

## Testing Strategy
- **Unit Tests**: `frontend/lib/map-kit/exporter.test.ts`
- **Mocks**:
  - Mock `HTMLCanvasElement` and `CanvasRenderingContext2D`.
  - Mock `maplibregl.Map` and its canvas methods.
- **Verifications**:
  - Ensure correct canvas dimensions based on DPI.
  - Verify `composeLayout` calls drawing methods (`fillText`, `strokeRect`) with expected coordinates.
  - Verify `downloadBlob` creates a link and clicks it.

## Data Flow
1. User triggers "Export".
2. App calls `captureMapCanvas`.
3. App calls `composeLayout` with the captured canvas and user-provided metadata.
4. App calls `downloadBlob` with the final canvas converted to a Blob.
