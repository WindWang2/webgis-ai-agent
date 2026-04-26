# Layer Style Editor Design

**Date:** 2026-04-27
**Status:** Approved

## Goal

Add a slide-in style editor panel for map layers. Users can change colors, opacity, stroke width, fill toggle, render mode (vector/heatmap/grid), and rename layers — all applied immediately to the map without a save step.

## Architecture

```
LayerCard (edit button) → setEditingLayerId(layer.id)
                              ↓
LayerStylePanel reads layer from store → renders controls by type
                              ↓
updateLayer(id, { style, opacity, name }) → Zustand store
                              ↓
map-panel.tsx useEffect detects change → map.setPaintProperty()
```

Single component approach: `LayerStylePanel` handles all layer types internally, conditionally rendering sections based on `layer.type`.

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `frontend/components/hud/layer-style-panel.tsx` | Slide-in panel with per-type style controls |

### Modified Files

| File | Change |
|------|--------|
| `frontend/lib/store/useHudStore.ts` | Add `editingLayerId` + `setEditingLayerId` |
| `frontend/components/layer-card.tsx` | Wire edit button to `setEditingLayerId` |
| `frontend/components/panel/results-panel.tsx` or `frontend/app/page.tsx` | Render `LayerStylePanel` when editing |

## Store Extension

`useHudStore` additions:

```ts
editingLayerId: string | null;
setEditingLayerId: (id: string | null) => void;
```

`setEditingLayerId: (id) => set({ editingLayerId: id })` — null means panel closed.

## Component: LayerStylePanel

### Layout

```
┌─────────────────────────────┐
│ ← 图层样式          [Close] │  Header with back arrow + close
├─────────────────────────────┤
│ [Layer Name]                │  Inline editable name
│ 类型: 矢量  分组: analysis  │  Type badge + group tag
├─────────────────────────────┤
│                             │
│  填充颜色  [■ color picker] │  Fill color (vector only)
│  描边颜色  [■ color picker] │  Stroke color (vector only)
│  描边宽度  ───○──── 2px     │  Stroke width slider (vector only)
│  填充开关  [● ON]           │  Fill on/off toggle (vector only)
│                             │
│  渲染模式                   │  Render mode switch (vector only)
│  [矢量] [热力] [格网]       │  Three-button toggle
│                             │
│  透明度   ───○──── 85%      │  Opacity slider (all types)
│                             │
├─────────────────────────────┤
│  色带选择 (heatmap only)    │  Palette dropdown
│  [inferno ▼]                │
│  热力半径 ───○──── 30px     │  Radius slider (heatmap only)
│  热力强度 ───○──── 1.0      │  Weight/intensity slider
└─────────────────────────────┘
```

### Sections by Layer Type

**Common (all types):**
- Inline name editor (same pattern as layer-card)
- Type badge + group tag (read-only)
- Opacity slider (0–100%, step 5%)

**Vector (`layer.type === 'vector'`):**
- Fill color: `<input type="color">` → updates `layer.style.color`
- Stroke color: `<input type="color">` → updates `layer.style.strokeColor`
- Stroke width: range slider 0–10 → updates `layer.style.strokeWidth`
- Fill toggle: switch → updates `layer.style.fill` (boolean)
- Render mode: 3-button group (矢量/热力/格网) → updates `layer.style.renderType`

**Raster/Tile (`layer.type === 'raster' | 'tile'`):**
- Opacity only (no color controls)

**Heatmap (`layer.type === 'heatmap'`):**
- Palette selector: dropdown with presets (inferno, viridis, ylorrd, spectral, blues) → updates `layer.style.palette`
- Radius slider: 5–100 → updates `layer.style.radius`
- Intensity slider: 0.1–3.0 → updates `layer.style.intensity`

### Color Picker

Native `<input type="color">` wrapped in HUD-styled container:

```tsx
<label className="flex items-center gap-2">
  <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-white/10">
    <input type="color" value={color} onChange={handleColorChange}
      className="absolute inset-0 w-full h-full cursor-pointer" />
  </div>
  <span className="text-[10px] text-white/40 font-mono">{color}</span>
</label>
```

### Render Mode Switch

Three-button toggle for vector layers only:

```tsx
<div className="flex gap-1">
  {['vector', 'heatmap', 'grid'].map(mode => (
    <button key={mode} onClick={() => updateStyle({ renderType: mode })}
      className={`px-2 py-1 text-[10px] rounded ${
        currentMode === mode ? 'bg-hud-cyan/20 text-hud-cyan' : 'text-white/30 hover:text-white/50'
      }`}>
      {modeLabel(mode)}
    </button>
  ))}
</div>
```

### Heatmap Palette Selector

Dropdown with color band preview:

```tsx
const PALETTES = {
  inferno: { label: 'Inferno', colors: ['#000004', '#420a68', '#932667', '#dd513a', '#fca50a', '#fcffa4'] },
  viridis: { label: 'Viridis', colors: ['#440154', '#31688e', '#35b779', '#fde725'] },
  ylorrd:  { label: 'YlOrRd', colors: ['#ffffcc', '#fd8d3c', '#bd0026'] },
  spectral: { label: 'Spectral', colors: ['#9e0142', '#fdae61', '#ffffbf', '#abd9e9', '#5e4fa2'] },
  blues:   { label: 'Blues', colors: ['#f7fbff', '#6baed6', '#08306b'] },
};
```

Each palette option rendered as a horizontal gradient bar + label.

## Data Flow

1. User clicks edit button on `LayerCard` → `setEditingLayerId(layer.id)`
2. `LayerStylePanel` reads `editingLayerId` from store, finds matching layer
3. User changes a value → `updateLayer(id, { style: { ...layer.style, color: newColor } })`
4. Zustand updates → `map-panel.tsx` useEffect fires on `layers` change
5. MapLibre `setPaintProperty()` applies immediately — no save/confirm step needed

## Integration Points

### layer-card.tsx

The edit button already exists but does nothing. Wire it:

```tsx
// Before:
onEdit: _onEdit,
// After:
import { useHudStore } from '@/lib/store/useHudStore';

// In component body:
const setEditingLayerId = useHudStore(s => s.setEditingLayerId);

// In JSX, replace the edit button's onClick:
onClick={() => setEditingLayerId(layer.id)}
```

Remove the `onEdit` prop from `LayerCardProps` since we use the store directly.

### results-panel.tsx (or page.tsx)

Render `LayerStylePanel` in the right panel area when a layer is being edited:

```tsx
{editingLayerId && <LayerStylePanel />}
```

The panel slides in from the right over the results panel, or in a dedicated panel slot. When `setEditingLayerId(null)` is called (close button or clicking outside), the panel hides.

### map-panel.tsx

**No changes needed.** The existing useEffect already handles:
- `layer.style.color` → `setPaintProperty("fill-color", color)`
- `layer.opacity` → `setPaintProperty("*-opacity", opacity)`
- `layer.style.renderType` → switches between vector/heatmap/grid rendering

The only gap is that `map-panel.tsx` doesn't yet handle `strokeColor` and `strokeWidth` from `layer.style`. This needs a small addition to the paint property logic for LineString layers.

## Style Extension

Current `LayerStyle` interface:

```ts
export interface LayerStyle {
  color?: string;
  renderType?: 'heatmap' | 'grid';
  [key: string]: unknown;
}
```

Extended:

```ts
export interface LayerStyle {
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  fill?: boolean;
  renderType?: 'heatmap' | 'grid' | 'vector';
  palette?: string;
  radius?: number;
  intensity?: number;
  [key: string]: unknown;
}
```

## Verification

1. Click edit button on a vector layer → panel slides in with correct layer info
2. Change fill color → map updates immediately
3. Change stroke color/width → map line styles update
4. Toggle fill off → polygon fills disappear, outlines remain
5. Switch render mode to heatmap → layer re-renders as heatmap
6. Click close → panel closes, map state preserved
7. Edit a raster layer → only opacity slider shown
8. Edit a heatmap layer → palette/radius/intensity controls shown
