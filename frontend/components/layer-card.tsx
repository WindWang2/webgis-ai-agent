'use client';
import React, { memo, useState, useRef, useEffect } from 'react';
import { Eye, EyeOff, Trash2, GripVertical, Check, X, Type } from 'lucide-react';
import type { Layer } from '@/lib/types/layer';

interface LayerCardProps {
  layer: Layer;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onUpdate?: (id: string, updates: Partial<Layer>) => void;
  dragHandleProps?: React.HTMLAttributes<HTMLDivElement>;
}

const TYPE_STYLES: Record<string, { bg: string; text: string; border: string; dot: string }> = {
  vector: { bg: 'bg-hud-cyan/10', text: 'text-hud-cyan', border: 'border-hud-cyan/20', dot: 'bg-hud-cyan' },
  raster: { bg: 'bg-orange-500/10', text: 'text-orange-400', border: 'border-orange-500/20', dot: 'bg-orange-400' },
  tile: { bg: 'bg-purple-500/10', text: 'text-purple-400', border: 'border-purple-500/20', dot: 'bg-purple-400' },
  heatmap: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/20', dot: 'bg-emerald-400' },
};

const TYPE_LABELS: Record<string, string> = {
  vector: '矢量',
  raster: '栅格',
  tile: '瓦片',
  heatmap: '热力',
};

export const LayerCard = memo(function LayerCard({
  layer,
  onToggle,
  onDelete,
  onUpdate,
  dragHandleProps,
}: LayerCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [tempName, setTempName] = useState(layer.name);
  const inputRef = useRef<HTMLInputElement>(null);

  const typeStyle = TYPE_STYLES[layer.type] || TYPE_STYLES.vector;

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const handleStartRename = () => {
    setTempName(layer.name);
    setIsEditing(true);
  };

  const handleSaveRename = () => {
    if (tempName.trim() && onUpdate) {
      onUpdate(layer.id, { name: tempName.trim() });
    }
    setIsEditing(false);
  };

  const handleOpacityChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (onUpdate) {
      onUpdate(layer.id, { opacity: parseFloat(e.target.value) });
    }
  };

  const opacityPct = Math.round(layer.opacity * 100);

  return (
    <div className={`
      group relative rounded-xl overflow-hidden transition-all duration-200
      bg-white/[0.02] border border-white/[0.06]
      hover:bg-white/[0.04] hover:border-white/[0.1]
      ${layer.visible ? '' : 'opacity-50'}
    `}>
      {/* Gradient left border */}
      <div className={`absolute top-0 left-0 w-[3px] h-full transition-colors duration-300 ${layer.visible ? typeStyle.dot : 'bg-white/10'}`} />

      <div className="flex items-stretch pl-1">
        {/* Drag handle */}
        <div
          {...dragHandleProps}
          className="flex items-center px-1 cursor-grab active:cursor-grabbing text-white/10 hover:text-white/25 transition-colors"
        >
          <GripVertical size={14} />
        </div>

        <div className="flex-1 min-w-0 py-2.5 pr-3 pl-1">
          {/* Header row */}
          <div className="flex items-center gap-2 mb-2">
            {/* Visibility dot */}
            {layer.visible && (
              <div className={`w-1.5 h-1.5 rounded-full ${typeStyle.dot} shadow-[0_0_6px_currentColor]`} style={{ color: 'var(--hud-cyan, #00f2ff)' }} />
            )}

            {/* Name */}
            {isEditing ? (
              <div className="flex items-center gap-1 flex-1">
                <input
                  ref={inputRef}
                  type="text"
                  value={tempName}
                  onChange={(e) => setTempName(e.target.value)}
                  className="w-full text-[11px] bg-white/[0.06] border border-hud-cyan/30 rounded px-1.5 py-0.5 text-white/90 focus:outline-none focus:border-hud-cyan/60 transition-colors"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSaveRename();
                    if (e.key === 'Escape') setIsEditing(false);
                  }}
                />
                <button onClick={handleSaveRename} className="text-hud-cyan hover:scale-110 transition-transform"><Check size={12} /></button>
                <button onClick={() => setIsEditing(false)} className="text-white/20 hover:text-white/50"><X size={12} /></button>
              </div>
            ) : (
              <span
                className="text-[11px] font-medium text-white/70 truncate flex-1 cursor-pointer hover:text-white/90 transition-colors"
                onDoubleClick={handleStartRename}
                title={layer.name}
              >
                {layer.name}
              </span>
            )}

            {/* Type badge */}
            <span className={`
              text-[8px] px-1.5 py-0.5 rounded-full font-semibold uppercase tracking-wider
              ${typeStyle.bg} ${typeStyle.text} ${typeStyle.border} border
            `}>
              {TYPE_LABELS[layer.type] || layer.type}
            </span>
          </div>

          {/* Opacity slider */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1 h-1 bg-white/[0.06] rounded-full overflow-hidden">
              <div
                className={`absolute inset-y-0 left-0 rounded-full transition-all duration-100 ${typeStyle.dot} opacity-40`}
                style={{ width: `${opacityPct}%` }}
              />
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={layer.opacity}
                onChange={handleOpacityChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              />
            </div>
            <span className="text-[9px] text-white/20 w-7 text-right font-mono tabular-nums">
              {opacityPct}%
            </span>
          </div>

          {/* Action row */}
          <div className="flex items-center gap-1 mt-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <button
              onClick={onToggle.bind(null, layer.id)}
              className={`
                p-1 rounded transition-all
                ${layer.visible ? 'text-hud-cyan/60 hover:text-hud-cyan hover:bg-hud-cyan/10' : 'text-white/20 hover:text-white/40 hover:bg-white/[0.04]'}
              `}
              title={layer.visible ? '隐藏' : '显示'}
            >
              {layer.visible ? <Eye size={12} /> : <EyeOff size={12} />}
            </button>
            <button
              onClick={handleStartRename}
              className="p-1 rounded text-white/20 hover:text-white/50 hover:bg-white/[0.04] transition-all"
              title="重命名"
            >
              <Type size={12} />
            </button>
            <div className="flex-1" />
            <button
              onClick={onDelete.bind(null, layer.id)}
              className="p-1 rounded text-white/15 hover:text-red-400 hover:bg-red-500/10 transition-all"
              title="删除"
            >
              <Trash2 size={12} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});
