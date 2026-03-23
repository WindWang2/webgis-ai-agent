'use client';

import { useState, useEffect } from 'react';
import { Eye, EyeOff, Trash2, Layers, Loader2 } from 'lucide-react';
import { LayerItem } from './layer-item';

export interface Layer {
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

interface LayerListProps {
  layers?: Layer[];
  onLayerToggle?: (layerId: string, visible: boolean) => void;
  onLayerOpacityChange?: (layerId: string, opacity: number) => void;
  onLayerDelete?: (layerId: string) => void;
  onLayerSelect?: (layerId: string) => void;
  selectedLayerId?: string;
  refreshInterval?: number;
}

export function LayerList({
  layers: initialLayers,
  onLayerToggle,
  onLayerOpacityChange,
  onLayerDelete,
  onLayerSelect,
  selectedLayerId,
  refreshInterval = 5000,
}: LayerListProps) {
  const [layers, setLayers] = useState<Layer[]>(initialLayers || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchLayers = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/layers');
      if (!response.ok) throw new Error('获取图层列表失败');
      const data = await response.json();
      setLayers(data.layers || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!initialLayers) {
      fetchLayers();
      const interval = setInterval(fetchLayers, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [initialLayers, refreshInterval]);

  const handleToggle = (layerId: string, currentVisible: boolean) => {
    const newVisible = !currentVisible;
    setLayers(prev => prev.map(l =>
      l.id === layerId ? { ...l, isVisible: newVisible } : l
    ));
    onLayerToggle?.(layerId, newVisible);
  };

  const handleOpacityChange = (layerId: string, opacity: number) => {
    setLayers(prev => prev.map(l =>
      l.id === layerId ? { ...l, opacity } : l
    ));
    onLayerOpacityChange?.(layerId, opacity);
  };

  const handleDelete = async (layerId: string) => {
    if (!confirm('确定要删除此图层吗？')) return;

    try {
      const response = await fetch(`/api/layers/${layerId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('删除失败');
      setLayers(prev => prev.filter(l => l.id !== layerId));
      onLayerDelete?.(layerId);
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    }
  };

  const handleSelect = (layerId: string) => {
    onLayerSelect?.(layerId);
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
          <Layers className="h-5 w-5" />
          图层列表
        </h3>
        <span className="text-sm text-gray-500">
          {layers.length} 个图层
        </span>
      </div>

      {loading && !initialLayers && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {layers.length === 0 && !loading ? (
        <div className="text-center py-8 text-gray-500 text-sm">
          暂无图层，请上传新图层
        </div>
      ) : (
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {layers.map(layer => (
            <LayerItem
              key={layer.id}
              layer={layer}
              isSelected={selectedLayerId === layer.id}
              formatFileSize={formatFileSize}
              onToggle={() => handleToggle(layer.id, layer.isVisible)}
              onOpacityChange={(opacity) => handleOpacityChange(layer.id, opacity)}
              onDelete={() => handleDelete(layer.id)}
              onSelect={() => handleSelect(layer.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
