'use client';

import React, { useState, useCallback } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';
import { GripVertical, Trash2 } from 'lucide-react';
import type { Layer } from '@/lib/types/layer';
import {
  commitLayerPresentation,
  removeLayerAndCommit,
  reorderLayersAndCommit,
} from '@/lib/mapspec/user-mutation';

export function LayerManagement() {
  const layers = useHudStore((s) => s.layers);
  const toggleLayer = useHudStore((s) => s.toggleLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);

  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  const handleDragStart = useCallback(
    (e: React.DragEvent, index: number) => {
      setDragIndex(index);
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(index));
    },
    [],
  );

  const handleDragOver = useCallback(
    (e: React.DragEvent, index: number) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      setDragOverIndex(index);
    },
    [],
  );

  const handleDragLeave = useCallback(() => {
    setDragOverIndex(null);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent, dropIndex: number) => {
      e.preventDefault();
      if (dragIndex === null || dragIndex === dropIndex) {
        setDragIndex(null);
        setDragOverIndex(null);
        return;
      }
      const reordered = [...layers];
      const [moved] = reordered.splice(dragIndex, 1);
      reordered.splice(dropIndex, 0, moved);
      void reorderLayersAndCommit(reordered);
      setDragIndex(null);
      setDragOverIndex(null);
    },
    [dragIndex, layers],
  );

  const handleDragEnd = useCallback(() => {
    setDragIndex(null);
    setDragOverIndex(null);
  }, []);

  const getTypeBadgeColor = (type: Layer['type']) => {
    switch (type) {
      case 'vector':
        return { bg: 'rgba(59,130,246,0.08)', color: '#3b82f6' };
      case 'raster':
        return { bg: 'rgba(249,115,22,0.08)', color: '#f97316' };
      case 'tile':
        return { bg: 'rgba(139,92,246,0.08)', color: '#8b5cf6' };
      case 'heatmap':
        return { bg: 'rgba(239,68,68,0.08)', color: '#ef4444' };
      default:
        return { bg: 'var(--theme-bg-muted)', color: 'var(--theme-text-muted)' };
    }
  };

  const getLayerColor = (layer: Layer) => {
    return layer.style?.color || '#94a3b8';
  };

  const getFeatureCount = (layer: Layer) => {
    const src = layer.source;
    if (src && typeof src === 'object' && 'features' in src) {
      return (src as { features: unknown[] }).features?.length ?? 0;
    }
    return null;
  };

  return (
    <div className="flex flex-col gap-4">
      <STitle title="图层管理" sub="Layer Management" />

      {layers.length === 0 ? (
        <div className="text-[14px] text-[var(--theme-text-muted)] italic py-6 text-center">
          No layers loaded
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {layers.map((layer, index) => {
            const isDragging = dragIndex === index;
            const isDragOver = dragOverIndex === index;
            const badge = getTypeBadgeColor(layer.type);
            const featureCount = getFeatureCount(layer);

            return (
              <div
                key={layer.id}
                draggable
                onDragStart={(e) => handleDragStart(e, index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, index)}
                onDragEnd={handleDragEnd}
                className="flex items-center gap-2 rounded-lg border bg-[var(--theme-bg-subtle)] px-3 py-2 transition-all cursor-default"
                style={{
                  borderColor: isDragOver
                    ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 30%, transparent)'
                    : 'var(--theme-border)',
                  backgroundColor: isDragOver
                    ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 4%, transparent)'
                    : 'var(--theme-bg-subtle)',
                  opacity: isDragging ? 0.4 : layer.visible ? 1 : 0.55,
                  boxShadow: isDragOver
                    ? '0 0 0 1px color-mix(in srgb, var(--agent-accent, #16a34a) 20%, transparent)'
                    : 'none',
                }}
              >
                {/* Drag handle */}
                <div className="cursor-grab active:cursor-grabbing text-[var(--theme-text-subtle)] hover:text-[var(--theme-text-muted)]">
                  <GripVertical size={14} />
                </div>

                {/* Color dot */}
                <span
                  className="block rounded-full flex-shrink-0"
                  style={{
                    width: 10,
                    height: 10,
                    backgroundColor: getLayerColor(layer),
                  }}
                />

                {/* Name + type */}
                <div className="flex items-center gap-1.5 min-w-0 flex-1">
                  <span className="text-[14px] font-medium text-[var(--theme-text-primary)] truncate">
                    {layer.name}
                  </span>
                  <span
                    className="text-[14px] font-medium rounded-full px-1.5 py-0.5"
                    style={{
                      backgroundColor: badge.bg,
                      color: badge.color,
                    }}
                  >
                    {layer.type}
                  </span>
                  {featureCount !== null && (
                    <span className="text-[14px] text-[var(--theme-text-muted)]">
                      {featureCount} features
                    </span>
                  )}
                </div>

                {/* Opacity slider */}
                <div className="flex items-center gap-1.5 w-24 flex-shrink-0">
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round(layer.opacity * 100)}
                    onChange={(e) =>
                      updateLayer(layer.id, {
                        opacity: Number(e.target.value) / 100,
                      })
                    }
                    className="w-full h-1 rounded-full appearance-none cursor-pointer"
                    style={{
                      background: `linear-gradient(to right, var(--agent-accent, #16a34a) ${layer.opacity * 100}%, var(--theme-border-subtle) ${layer.opacity * 100}%)`,
                    }}
                    title={`Opacity: ${Math.round(layer.opacity * 100)}%`}
                  />
                </div>

                {/* Visibility toggle */}
                <ToggleSwitch
                  label={`显示图层：${layer.name}`}
                  checked={layer.visible}
                  onChange={() => {
                    toggleLayer(layer.id);
                    void commitLayerPresentation({
                      layerId: layer.id,
                      visible: !layer.visible,
                    });
                  }}
                />

                {/* Delete */}
                <button
                  onClick={() => { void removeLayerAndCommit(layer.id); }}
                  aria-label="删除图层"
                  className="text-[var(--theme-text-subtle)] hover:text-red-400 transition-colors p-0.5"
                  title="Remove layer"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
