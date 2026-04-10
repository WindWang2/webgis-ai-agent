'use client';
import React, { memo } from 'react';
import { Eye, EyeOff, Edit, Trash2 } from 'lucide-react';
import type { Layer } from '@/lib/types/layer';

interface LayerCardProps {
  layer: Layer;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onEdit: (layer: Layer) => void;
}

export const LayerCard = memo(function LayerCard({
  layer,
  onToggle,
  onDelete,
  onEdit,
}: LayerCardProps) {
  const handleToggle = () => onToggle(layer.id);
  const handleDelete = () => onDelete(layer.id);
  const handleEdit = () => onEdit(layer);

  return (
    <div className="border rounded-lg p-4 bg-white shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-gray-900">{layer.name}</h3>
        <span className="text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-600 capitalize">
          {layer.type}
        </span>
      </div>
      
      <div className="flex items-center gap-2 text-sm text-gray-500 mb-3">
        <button
          onClick={handleToggle}
          className="p-1 hover:bg-gray-100 rounded"
          aria-label={layer.visible ? 'Hide layer' : 'Show layer'}
        >
          {layer.visible ? <Eye size={16} /> : <EyeOff size={16} />}
        </button>
        
        <span className="opacity-70">
          {Math.round(layer.opacity * 100)}%
        </span>
        
        {layer.source && typeof layer.source === "object" && (
          <span className="ml-auto text-xs">
            {layer.source.features?.length ?? 0} 个要素
          </span>
        )}
      </div>

      <div className="flex gap-2 border-t pt-3">
        <button
          onClick={handleEdit}
          className="flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700 px-2 py-1 rounded hover:bg-blue-50"
        >
          <Edit size={14} />
          Edit
        </button>
        <button
          onClick={handleDelete}
          className="flex items-center gap-1 text-sm text-red-600 hover:text-red-700 px-2 py-1 rounded hover:bg-red-50"
        >
          <Trash2 size={14} />
          Delete
        </button>
      </div>
    </div>
  );
});