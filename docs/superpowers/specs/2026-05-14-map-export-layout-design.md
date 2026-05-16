# Professional Map Export & Layout Panel Design

## 1. Overview
The goal is to implement a professional cartographic export feature for the WebGIS AI Agent. It will allow users to visually configure and preview standard map elements (compass, scale bar, legend, title, subtitle, and watermark) directly on the map, and export high-quality (e.g., 300 DPI, specific paper sizes) outputs for professional use.

## 2. Architecture & Components

### 2.1 UI Layer: Sidebar Export Panel (`ExportPanel`)
A new dedicated panel/drawer (or tab in the left sidebar) specifically for layout control.
**Features:**
- **Text Inputs:** Main Title, Subtitle, Custom Watermark text.
- **Toggles:** Show/Hide Compass, Scale Bar, Legend.
- **Paper & Quality Controls:** Format (PNG/PDF), Paper Size (Screen, A4, A3), Orientation (Landscape/Portrait), DPI (96, 150, 300).
- **Action:** A prominent "Export Map" button.

### 2.2 Live Preview Layer (`MapDecorators` / `ExportMask`)
To provide a true WYSIWYG (What You See Is What You Get) experience:
- When the Export Panel is active, a CSS/SVG masking layer (`ExportMask`) will overlay the main MapLibre canvas to show exactly which bounds of the map will be cropped for the chosen paper ratio.
- The map view acts as a live preview. Titles, sub-titles, compass, scale, and watermarks will be rendered as absolutely positioned React components over the map based on the current export state, avoiding the need for a secondary isolated preview map.

### 2.3 State Management (`useHudStore` Extension)
The HUD state will be expanded to manage the layout configurations globally:
```typescript
interface ExportSettings {
  isExportMode: boolean; // Enables the WYSIWYG mask and UI decorators
  title: string;
  subtitle: string;
  showWatermark: boolean;
  showCompass: boolean;
  showScale: boolean;
  showLegend: boolean;
  paperSize: 'screen' | 'A4';
  orientation: 'landscape' | 'portrait';
  dpi: number;
  format: 'png' | 'pdf';
}
```

### 2.4 Rendering & Export Engine (`MapActionHandler` Refactor)
Currently, `export_map` relies on `map.getCanvas()` and `Canvas 2D` commands to overlay the UI.
For high DPI and custom aspect ratio exports:
- The system will temporarily adjust the MapLibre container dimensions (or use a hidden off-screen map instance if necessary for extreme resolutions) to match the target paper size/ratio.
- The `export_map` action logic will be updated to draw the user-configured text (titles, watermarks) and standard cartography objects (scale, compass, legend) accurately scaled to the target DPI.
- PDF generation will continue to rely on the backend API (`/api/v1/export/pdf`), passing the accurately composed base PNG.

## 3. Data Flow
1. **User interacts with ExportPanel:** User modifies the title or changes paper size to "A4".
2. **State Updates:** `useHudStore` updates `exportSettings`.
3. **Live Feedback:** The `ExportMask` component recalculates the crop ratio based on window size vs. A4 aspect ratio, darkening the out-of-bounds map areas. The Title component updates its text.
4. **Trigger Export:** User clicks "Export". `ExportPanel` dispatches the `export_map` action.
5. **Compositing:** `MapActionHandler` executes the canvas compositing using the current parameters in the store, requests the high-res screenshot from MapLibre, draws overlays, and saves/uploads the result.

## 4. Scope & Edge Cases
- **Resolution Limits:** WebGL context limits (usually 4096px or 8192px) restrict maximum DPI rendering. If a user requests A3 at 300 DPI, we might hit WebGL constraints. The system should gracefully fall back to the maximum safe resolution if it exceeds context limits.
- **Independent from AI:** This feature is primarily a UI/UX improvement and user-driven tool. While the AI can trigger `export_map` natively, this panel is for manual human adjustments before final export.

## 5. Review Checklist (Self-Review completed)
- [x] Clear architectural boundaries? (Yes, clear separation between UI, State, and Canvas compositor).
- [x] Scope defined? (Yes, focused entirely on map layout and high-res canvas extraction).
- [x] Ambiguity resolved? (Yes, WYSIWYG is chosen over isolated modal, ensuring smooth user experience).olved? (Yes, WYSIWYG is chosen over isolated modal, ensuring smooth user experience).