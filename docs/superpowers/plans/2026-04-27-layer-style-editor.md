# Layer Style Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a slide-in style editor panel so users can change layer colors, opacity, stroke width, fill toggle, render mode, and rename — applied immediately to the map.

**Architecture:** Single `LayerStylePanel` component reads the active layer from Zustand store via `editingLayerId`, renders per-type controls, and writes back via `updateLayer()`. MapLibre already reacts to `layer.style` and `layer.opacity` changes in its useEffect — no backend changes needed.

**Tech Stack:** React 18, Zustand, Framer Motion (animation), Lucide icons, Tailwind CSS, native `<input type="color">`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `frontend/components/hud/layer-style-panel.tsx` (new) | Slide-in panel with per-type style controls |
| `frontend/lib/store/useHudStore.ts` (modify) | Add `editingLayerId` + `setEditingLayerId` |
| `frontend/lib/types/layer.ts` (modify) | Extend `LayerStyle` with stroke/fill/palette fields |
| `frontend/components/layer-card.tsx` (modify) | Wire edit button to store |
| `frontend/components/map/draggable-layer-list.tsx` (modify) | Remove legacy `onEdit` prop |
| `frontend/components/map/map-panel.tsx` (modify) | Handle new style fields (`strokeColor`, `strokeWidth`, `fill`) in paint properties |
| `frontend/components/panel/results-panel.tsx` (modify) | Render `LayerStylePanel` when editing |

---

### Task 1: Extend LayerStyle Type

**Files:**
- Modify: `frontend/lib/types/layer.ts`

- [ ] **Step 1: Extend the LayerStyle interface**

Current `LayerStyle` at `frontend/lib/types/layer.ts:3-7`:

```ts
export interface LayerStyle {
  color?: string;
  renderType?: 'heatmap' | 'grid';
  [key: string]: unknown;
}
```

Replace with:

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

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No new errors related to `LayerStyle`

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/types/layer.ts
git commit -m "feat: extend LayerStyle with stroke, fill, palette fields"
```

---

### Task 2: Add editingLayerId to Store

**Files:**
- Modify: `frontend/lib/store/useHudStore.ts`

- [ ] **Step 1: Add state field and setter**

In `frontend/lib/store/useHudStore.ts`, add to the `HudState` interface (after line 44, near the other layer fields):

```ts
  /* ─── Layer Editing ─── */
  editingLayerId: string | null;
  setEditingLayerId: (id: string | null) => void;
```

In the `create<HudState>((set) => ({...}))` body (after the `clearLayers` line at ~127):

```ts
  /* ─── Layer Editing ─── */
  editingLayerId: null,
  setEditingLayerId: (id) => set({ editingLayerId: id }),
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/store/useHudStore.ts
git commit -m "feat: add editingLayerId to Zustand store"
```

---

### Task 3: Create LayerStylePanel Component

**Files:**
- Create: `frontend/components/hud/layer-style-panel.tsx`

- [ ] **Step 1: Write the component**

```tsx
'use client';
import { memo, useState, useRef, useEffect } from 'react';
import { X, Palette, Minus, Plus, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useHudStore } from '@/lib/store/useHudStore';
import type { LayerStyle } from '@/lib/types/layer';

const PALETTES: Record<string, { label: string; colors: string[] }> = {
  inferno: { label: 'Inferno', colors: ['#000004', '#420a68', '#932667', '#dd513a', '#fca50a', '#fcffa4'] },
  viridis: { label: 'Viridis', colors: ['#440154', '#31688e', '#35b779', '#fde725'] },
  ylorrd:  { label: 'YlOrRd', colors: ['#ffffcc', '#fd8d3c', '#bd0026'] },
  spectral: { label: 'Spectral', colors: ['#9e0142', '#fdae61', '#ffffbf', '#abd9e9', '#5e4fa2'] },
  blues:   { label: 'Blues', colors: ['#f7fbff', '#6baed6', '#08306b'] },
};

const MODE_LABELS: Record<string, string> = { vector: '矢量', heatmap: '热力', grid: '格网' };

export const LayerStylePanel = memo(function LayerStylePanel() {
  const editingLayerId = useHudStore((s) => s.editingLayerId);
  const layers = useHudStore((s) => s.layers);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const setEditingLayerId = useHudStore((s) => s.setEditingLayerId);

  const layer = layers.find((l) => l.id === editingLayerId);

  const updateStyle = (patch: Partial<LayerStyle>) => {
    if (!layer) return;
    updateLayer(layer.id, { style: { ...layer.style, ...patch } });
  };

  const [tempName, setTempName] = useState('');
  const [isRenaming, setIsRenaming] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming && nameRef.current) {
      nameRef.current.focus();
      nameRef.current.select();
    }
  }, [isRenaming]);

  if (!layer) return null;

  const style = layer.style || {};
  const color = style.color || '#00f2ff';
  const strokeColor = style.strokeColor || color;
  const strokeWidth = style.strokeWidth ?? 2;
  const fillEnabled = style.fill !== false;
  const renderType = style.renderType || 'vector';
  const palette = style.palette || 'inferno';
  const radius = style.radius ?? 30;
  const intensity = style.intensity ?? 1;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 40, opacity: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        className="flex flex-col h-full"
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06]">
          <button
            onClick={() => setEditingLayerId(null)}
            className="text-white/30 hover:text-white/60 transition-colors"
          >
            <X size={16} />
          </button>
          <span className="text-[11px] font-display font-semibold text-white/50 uppercase tracking-wider">
            图层样式
          </span>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {/* Name */}
          <div>
            <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">名称</label>
            {isRenaming ? (
              <div className="flex items-center gap-1">
                <input
                  ref={nameRef}
                  value={tempName}
                  onChange={(e) => setTempName(e.target.value)}
                  className="flex-1 text-[11px] bg-white/[0.06] border border-hud-cyan/30 rounded px-2 py-1 text-white/90 focus:outline-none focus:border-hud-cyan/60"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      if (tempName.trim()) updateLayer(layer.id, { name: tempName.trim() });
                      setIsRenaming(false);
                    }
                    if (e.key === 'Escape') setIsRenaming(false);
                  }}
                />
                <button onClick={() => { if (tempName.trim()) updateLayer(layer.id, { name: tempName.trim() }); setIsRenaming(false); }}
                  className="text-hud-cyan"><Check size={14} /></button>
              </div>
            ) : (
              <div
                className="text-[11px] text-white/70 cursor-pointer hover:text-white/90 transition-colors"
                onDoubleClick={() => { setTempName(layer.name); setIsRenaming(true); }}
              >
                {layer.name}
                <span className="text-white/15 ml-2 text-[9px]">双击编辑</span>
              </div>
            )}
          </div>

          {/* Type & Group info */}
          <div className="flex items-center gap-2">
            <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-hud-cyan/10 text-hud-cyan border border-hud-cyan/20 font-semibold uppercase">
              {layer.type}
            </span>
            {layer.group && (
              <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-white/[0.04] text-white/25 border border-white/[0.06]">
                {layer.group}
              </span>
            )}
          </div>

          {/* === VECTOR CONTROLS === */}
          {layer.type === 'vector' && (
            <>
              {/* Fill Color */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">填充颜色</label>
                <div className="flex items-center gap-2">
                  <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-white/10">
                    <input type="color" value={color}
                      onChange={(e) => updateStyle({ color: e.target.value })}
                      className="absolute inset-0 w-full h-full cursor-pointer" />
                  </div>
                  <span className="text-[10px] text-white/30 font-mono">{color}</span>
                </div>
              </div>

              {/* Stroke Color */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">描边颜色</label>
                <div className="flex items-center gap-2">
                  <div className="relative w-7 h-7 rounded-lg overflow-hidden border border-white/10">
                    <input type="color" value={strokeColor}
                      onChange={(e) => updateStyle({ strokeColor: e.target.value })}
                      className="absolute inset-0 w-full h-full cursor-pointer" />
                  </div>
                  <span className="text-[10px] text-white/30 font-mono">{strokeColor}</span>
                </div>
              </div>

              {/* Stroke Width */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">
                  描边宽度 <span className="text-white/15 font-mono">{strokeWidth}px</span>
                </label>
                <input type="range" min={0} max={10} step={0.5} value={strokeWidth}
                  onChange={(e) => updateStyle({ strokeWidth: parseFloat(e.target.value) })}
                  className="w-full accent-hud-cyan" />
              </div>

              {/* Fill Toggle */}
              <div className="flex items-center justify-between">
                <label className="text-[9px] text-white/25 uppercase tracking-wider">填充开关</label>
                <button
                  onClick={() => updateStyle({ fill: !fillEnabled })}
                  className={`w-8 h-4 rounded-full transition-colors relative ${fillEnabled ? 'bg-hud-cyan/40' : 'bg-white/10'}`}
                >
                  <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
                    fillEnabled ? 'left-[18px] bg-hud-cyan' : 'left-0.5 bg-white/30'
                  }`} />
                </button>
              </div>

              {/* Render Mode Switch */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">渲染模式</label>
                <div className="flex gap-1">
                  {(['vector', 'heatmap', 'grid'] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => updateStyle({ renderType: mode })}
                      className={`flex-1 px-2 py-1.5 text-[9px] rounded-lg font-semibold transition-colors ${
                        renderType === mode
                          ? 'bg-hud-cyan/20 text-hud-cyan'
                          : 'text-white/20 hover:text-white/40 hover:bg-white/[0.03]'
                      }`}
                    >
                      {MODE_LABELS[mode]}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* === HEATMAP CONTROLS === */}
          {layer.type === 'heatmap' && (
            <>
              {/* Palette */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block flex items-center gap-1">
                  <Palette size={10} /> 色带
                </label>
                <div className="space-y-1">
                  {Object.entries(PALETTES).map(([key, p]) => (
                    <button
                      key={key}
                      onClick={() => updateStyle({ palette: key })}
                      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors ${
                        palette === key ? 'bg-hud-cyan/10 ring-1 ring-hud-cyan/30' : 'hover:bg-white/[0.03]'
                      }`}
                    >
                      <div className="flex-1 h-3 rounded-full overflow-hidden flex">
                        {p.colors.map((c, i) => (
                          <div key={i} className="flex-1" style={{ backgroundColor: c }} />
                        ))}
                      </div>
                      <span className={`text-[9px] ${palette === key ? 'text-hud-cyan' : 'text-white/25'}`}>
                        {p.label}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Radius */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">
                  热力半径 <span className="text-white/15 font-mono">{radius}px</span>
                </label>
                <input type="range" min={5} max={100} step={1} value={radius}
                  onChange={(e) => updateStyle({ radius: parseInt(e.target.value) })}
                  className="w-full accent-hud-cyan" />
              </div>

              {/* Intensity */}
              <div>
                <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">
                  热力强度 <span className="text-white/15 font-mono">{intensity.toFixed(1)}</span>
                </label>
                <input type="range" min={0.1} max={3} step={0.1} value={intensity}
                  onChange={(e) => updateStyle({ intensity: parseFloat(e.target.value) })}
                  className="w-full accent-hud-cyan" />
              </div>
            </>
          )}

          {/* === OPACITY (ALL TYPES) === */}
          <div>
            <label className="text-[9px] text-white/25 uppercase tracking-wider mb-1.5 block">
              透明度 <span className="text-white/15 font-mono">{Math.round(layer.opacity * 100)}%</span>
            </label>
            <input type="range" min={0} max={1} step={0.05} value={layer.opacity}
              onChange={(e) => updateLayer(layer.id, { opacity: parseFloat(e.target.value) })}
              className="w-full accent-hud-cyan" />
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
});
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/components/hud/layer-style-panel.tsx
git commit -m "feat: add LayerStylePanel component with per-type controls"
```

---

### Task 4: Wire LayerCard Edit Button to Store

**Files:**
- Modify: `frontend/components/layer-card.tsx`
- Modify: `frontend/components/map/draggable-layer-list.tsx`

- [ ] **Step 1: Update LayerCard to use store directly**

In `frontend/components/layer-card.tsx`:

Remove the `onEdit` prop from `LayerCardProps` (line 10):
```ts
// Remove this line:
  onEdit: (layer: Layer) => void;
```

Add store import at top:
```ts
import { useHudStore } from '@/lib/store/useHudStore';
```

In the component body, add after line 37 (`void _onEdit;` — remove that line too):
```ts
const setEditingLayerId = useHudStore((s) => s.setEditingLayerId);
```

Remove the `_onEdit` parameter from the destructuring (line 33):
```ts
// Before:
onEdit: _onEdit,
// After: remove this line entirely
```

Remove `void _onEdit;` (line 37).

- [ ] **Step 2: Update DraggableLayerList**

In `frontend/components/map/draggable-layer-list.tsx`:

Remove `onEdit` from `SortableLayerItemProps` (no `onEdit` prop there — it's passed inline).

In the `SortableLayerItem` component (line 53), change:
```tsx
// Before:
onEdit={() => {}} // Legacy
// After: remove the onEdit prop entirely from LayerCard
```

So the `LayerCard` usage becomes:
```tsx
<LayerCard
  layer={layer}
  onToggle={onToggle}
  onDelete={onDelete}
  onUpdate={onUpdate}
  dragHandleProps={{ ...attributes, ...listeners }}
/>
```

- [ ] **Step 3: Update DataHud/ResultsPanel**

In `frontend/components/panel/results-panel.tsx`, the `DraggableLayerList` usage does not pass `onEdit` — no changes needed there.

Check if `onEdit` is passed from `page.tsx`:

In `frontend/app/page.tsx`, find where `DataHud` or `DraggableLayerList` is rendered. If `onEdit` is passed as a prop, remove it.

In `frontend/app/page.tsx`, find the `onEditLayer={() => {}}` prop and remove it entirely from the JSX.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/components/layer-card.tsx frontend/components/map/draggable-layer-list.tsx frontend/app/page.tsx frontend/components/panel/results-panel.tsx
git commit -m "feat: wire layer edit button to editingLayerId store"
```

---

### Task 5: Render LayerStylePanel in Results Panel

**Files:**
- Modify: `frontend/components/panel/results-panel.tsx`

- [ ] **Step 1: Import and render LayerStylePanel**

In `frontend/components/panel/results-panel.tsx`:

Add import at top:
```ts
import { LayerStylePanel } from '@/components/hud/layer-style-panel';
```

Add to the store destructure (line 39-47):
```ts
editingLayerId: useHudStore((s) => s.editingLayerId),
```

In the JSX, wrap the tab content area. Find the `<div className="flex-1 overflow-y-auto">` (line 124). Replace the content structure:

```tsx
      {/* Content */}
      <div className="flex-1 overflow-y-auto relative">
        {editingLayerId ? (
          <LayerStylePanel />
        ) : (
          <AnimatePresence mode="wait" custom={activeIndex}>
            {/* ... existing tab content stays the same ... */}
          </AnimatePresence>
        )}
      </div>
```

This replaces the existing `<div className="flex-1 overflow-y-auto">` block. The `AnimatePresence` block inside stays exactly the same — just wrapped in the conditional.

The tab bar above stays visible when the style panel is open, providing context. The panel slides in from the right via Framer Motion.

- [ ] **Step 2: Verify the app builds**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx next build 2>&1 | tail -20`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/components/panel/results-panel.tsx
git commit -m "feat: render LayerStylePanel in results panel when editing"
```

---

### Task 6: Handle New Style Fields in Map Rendering

**Files:**
- Modify: `frontend/components/map/map-panel.tsx`

- [ ] **Step 1: Add strokeColor/strokeWidth support**

In `frontend/components/map/map-panel.tsx`, find where paint properties are set for line features. Look for the line that sets `line-color` (around line 335):

```tsx
"line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], color],
```

Change `color` to also check `layer.style.strokeColor`:
```tsx
const strokeColor = layer.style?.strokeColor || layer.style?.color || "#00f2ff"
```

Then in the LineString paint section (around line 334-347), use `strokeColor` instead of `color`:
```tsx
"line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], strokeColor],
```

For stroke width, find the line that sets `line-width` (if it exists) or add it. After the `line-color` line, add or update:
```tsx
"line-width": layer.style?.strokeWidth ?? 2,
```

- [ ] **Step 2: Add fill toggle support**

For the polygon fill section (around line 313-320), wrap the fill color with the fill toggle:

```tsx
"fill-color": layer.style?.fill !== false
  ? ["coalesce", ["get", "fill_color"], color]
  : "rgba(0,0,0,0)",
```

When `fill` is false, the fill becomes transparent but the outline remains visible.

- [ ] **Step 3: Verify the app builds**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx next build 2>&1 | tail -20`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/components/map/map-panel.tsx
git commit -m "feat: handle strokeColor, strokeWidth, fill toggle in map rendering"
```

---

## Verification

1. Start frontend: `cd frontend && npm run dev`
2. Have at least one vector layer on the map
3. Click the edit button on a layer card → LayerStylePanel slides in
4. Change fill color → map updates immediately
5. Change stroke color/width → line styles update on map
6. Toggle fill off → polygon fills become transparent
7. Switch render mode to 热力 → layer re-renders as heatmap
8. Click close (X) → panel closes, style preserved
9. Edit a raster layer → only opacity slider shown
10. Edit a heatmap layer → palette/radius/intensity controls shown
