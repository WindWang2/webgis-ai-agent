'use client';

import { Eye, EyeOff, Trash2, File, ChevronDown, ChevronUp } from 'lucide-react';
import { useState } from 'react';

interface Layer {
  id: string;
  name: string;
  fileName: string;
  format: string;
  size: number;
  createdAt: string;
  bounds: [number, number, number, number];
  crs?: string;
  featureCount?: number;
  isVisible: boolean;
  opacity: number;
  thumbnail?: string;
}

interface LayerItemProps {
  layer: Layer;
  isSelected: boolean;
  formatFileSize: (bytes: number) => string;
  onToggle: () => void;
  onOpacityChange: (opacity: number) => void;
  onDelete: () => void;
  onSelect: () => void;
}

export function LayerItem({
  layer,
  isSelected,
  formatFileSize,
  onToggle,
  onOpacityChange,
  onDelete,
  onSelect,
}: LayerItemProps) {
  const [expanded, setExpanded] = useState(false);

  const formatTime = (isoString: string) => {
    return new Date(isoString).toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div
      className={`
        border rounded-lg transition-all cursor-pointer
        ${isSelected ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'}
      `}
      onClick={onSelect}
    >
      {/* Layer Header */}
      <div className="flex items-center gap-3 p-3">
        {/* Visibility Toggle */}
        <button
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          className={`
            p-1.5 rounded transition-colors
            ${layer.isVisible ? 'text-blue-600 hover:bg-blue-100' : 'text-gray-400 hover:bg-gray-100'}
          `}
        >
          {layer.isVisible ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
        </button>

        {/* File Icon */}
        <div className="p-1.5 bg-gray-100 rounded">
          <File className="h-4 w-4 text-gray-600" />
        </div>

        {/* Layer Info */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 truncate">{layer.name}</p>
          <p className="text-xs text-gray-500">{layer.format.toUpperCase()}</p>
        </div>

        {/* Opacity Slider */}
        <div className="w-20 hidden sm:block">
          <input
            type="range"
            min="0"
            max="100"
            value={layer.opacity * 100}
            onChange={(e) => onOpacityChange(Number(e.target.value) / 100)}
            onClick={(e) => e.stopPropagation()}
            className="w-full h-1 bg-gray-200 rounded-lg appearance-none cursor-pointer"
          />
        </div>

        {/* Expand Toggle */}
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
          className="p-1 text-gray-400 hover:text-gray-600"
        >
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>

        {/* Delete Button */}
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {/* Expanded Details */}
      {expanded && (
        <div className="px-3 pb-3 pt-0 border-t border-gray-100">
          <div className="grid grid-cols-2 gap-2 py-2 text-xs">
            <div>
              <span className="text-gray-500">文件名:</span>
              <p className="text-gray-700 truncate">{layer.fileName}</p>
            </div>
            <div>
              <span className="text-gray-500">大小:</span>
              <p className="text-gray-700">{formatFileSize(layer.size)}</p>
            </div>
            <div>
              <span className="text-gray-500">创建时间:</span>
              <p className="text-gray-700">{formatTime(layer.createdAt)}</p>
            </div>
            {layer.crs && (
              <div>
                <span className="text-gray-500">坐标系:</span>
                <p className="text-gray-700">{layer.crs}</p>
              </div>
            )}
            {layer.featureCount !== undefined && (
              <div>
                <span className="text-gray-500">要素数量:</span>
                <p className="text-gray-700">{layer.featureCount.toLocaleString()}</p>
              </div>
            )}
            <div>
              <span className="text-gray-500">范围:</span>
              <p className="text-gray-700">
                [{layer.bounds[0].toFixed(2)}, {layer.bounds[1].toFixed(2)},
                 {layer.bounds[2].toFixed(2)}, {layer.bounds[3].toFixed(2)}]
              </p>
            </div>
          </div>

          {/* Opacity Control (Mobile) */}
          <div className="sm:hidden pt-2">
            <label className="text-xs text-gray-500">透明度: {Math.round(layer.opacity * 100)}%</label>
            <input
              type="range"
              min="0"
              max="100"
              value={layer.opacity * 100}
              onChange={(e) => onOpacityChange(Number(e.target.value) / 100)}
              className="w-full h-1 bg-gray-200 rounded-lg appearance-none cursor-pointer"
            />
          </div>
        </div>
      )}
    </div>
  );
}
