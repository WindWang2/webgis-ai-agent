'use client';
import { memo, useState, useRef, useEffect } from 'react';
import { X, Palette } from 'lucide-react';
import { motion } from 'framer-motion';
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
  const editingLayerId = useHudStore((s: any) => s.editingLayerId);
  const layers = useHudStore((s: any) => s.layers);
  const updateLayer = useHudStore((s: any) => s.updateLayer);
  const setEditingLayerId = useHudStore((s: any) => s.setEditingLayerId);

  const layer = layers.find((l: any) => l.id === editingLayerId);

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

        {/* Type & Group */}
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
  );
});
