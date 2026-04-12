'use client';
import React, { memo, useState } from 'react';
import { Eye, EyeOff, Edit, Trash2, GripVertical, Check, X } from 'lucide-react';
import type { Layer } from '@/lib/types/layer';

interface LayerCardProps {
  layer: Layer;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onEdit: (layer: Layer) => void; // Keep for backward compatibility if needed
  onUpdate?: (id: string, updates: Partial<Layer>) => void;
  dragHandleProps?: any;
}

export const LayerCard = memo(function LayerCard({
  layer,
  onToggle,
  onDelete,
  onEdit,
  onUpdate,
  dragHandleProps,
}: LayerCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [tempName, setTempName] = useState(layer.name);

  const handleToggle = () => onToggle(layer.id);
  const handleDelete = () => onDelete(layer.id);
  
  const handleStartRename = () => {
    setTempName(layer.name);
    setIsEditing(true);
  };

  const handleSaveRename = () => {
    if (onUpdate) onUpdate(layer.id, { name: tempName });
    setIsEditing(false);
  };

  const handleCancelRename = () => {
    setIsEditing(false);
  };

  const handleOpacityChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (onUpdate) {
      onUpdate(layer.id, { opacity: parseFloat(e.target.value) });
    }
  };

  return (
    <div className="group border rounded-lg p-3 bg-card hover:shadow-lg transition-all border-border/50 relative overflow-hidden">
      {/* 装饰线 */}
      <div className="absolute top-0 left-0 w-1 h-full bg-primary/20 group-hover:bg-primary transition-colors" />
      
      <div className="flex items-start gap-2">
        <div 
          {...dragHandleProps} 
          className="mt-1 cursor-grab active:cursor-grabbing text-muted-foreground/40 hover:text-primary transition-colors"
        >
          <GripVertical size={18} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            {isEditing ? (
              <div className="flex items-center gap-1 flex-1 mr-2">
                <input
                  type="text"
                  value={tempName}
                  onChange={(e) => setTempName(e.target.value)}
                  className="w-full text-sm bg-muted border border-primary/50 rounded px-1.5 py-0.5 focus:outline-none"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleSaveRename()}
                />
                <button onClick={handleSaveRename} className="text-primary hover:text-primary/80"><Check size={14} /></button>
                <button onClick={handleCancelRename} className="text-muted-foreground hover:text-foreground"><X size={14} /></button>
              </div>
            ) : (
              <h3 
                className="font-semibold text-sm truncate text-foreground/90 flex-1 cursor-pointer hover:text-primary transition-colors"
                onDoubleClick={handleStartRename}
              >
                {layer.name}
              </h3>
            )}
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 capitalize font-medium">
              {layer.type}
            </span>
          </div>
          
          <div className="flex flex-col gap-2 mt-2">
            {/* Opacity Slider */}
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground w-8 uppercase tracking-tighter">不透明</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={layer.opacity}
                onChange={handleOpacityChange}
                className="flex-1 h-1.5 bg-muted rounded-lg appearance-none cursor-pointer accent-primary"
              />
              <span className="text-[10px] text-muted-foreground w-6 text-right font-mono">
                {Math.round(layer.opacity * 100)}%
              </span>
            </div>

            <div className="flex items-center justify-between gap-2 mt-1">
              <div className="flex items-center gap-1">
                <button
                  onClick={handleToggle}
                  className={`p-1.5 rounded-md transition-colors ${layer.visible ? 'bg-primary/10 text-primary hover:bg-primary/20' : 'text-muted-foreground/50 hover:bg-muted'}`}
                  title={layer.visible ? '隐藏图层' : '显示图层'}
                >
                  {layer.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                </button>
                <button
                  onClick={handleStartRename}
                  className="p-1.5 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                  title="重命名"
                >
                  <Edit size={14} />
                </button>
              </div>

              <button
                onClick={handleDelete}
                className="p-1.5 rounded-md text-destructive/60 hover:bg-destructive/10 hover:text-destructive transition-colors ml-auto"
                title="删除图层"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});