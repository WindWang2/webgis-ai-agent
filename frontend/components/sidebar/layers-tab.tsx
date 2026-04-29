'use client';

import { useMemo, useState, useCallback } from 'react';
import { Eye, EyeOff, Trash2, GripVertical } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';

const GROUP_NAMES: Record<string, string> = {
  analysis: '分析结果',
  base: '底图',
  reference: '参考数据',
  default: '未分组',
};

export function LayersTab() {
  const layers = useHudStore((s) => s.layers);
  const toggleLayer = useHudStore((s) => s.toggleLayer);
  const removeLayer = useHudStore((s) => s.removeLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const reorderLayers = useHudStore((s) => s.reorderLayers);

  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const visibleCount = useMemo(
    () => layers.filter((l) => l.visible).length,
    [layers]
  );

  const totalFeatures = useMemo(() => {
    return layers.reduce((sum, l) => {
      if (l.source && typeof l.source === 'object' && 'features' in l.source) {
        return sum + ((l.source as any).features?.length ?? 0);
      }
      return sum;
    }, 0);
  }, [layers]);

  // Group layers
  const groups = useMemo(() => {
    const groupMap = new Map<string, any[]>();
    layers.forEach((layer) => {
      const key = layer.group || 'default';
      if (!groupMap.has(key)) groupMap.set(key, []);
      groupMap.get(key)!.push(layer);
    });
    const result: { name: string; layers: any[] }[] = [];
    groupMap.forEach((gLayers, key) => {
      result.push({ name: key, layers: gLayers });
    });
    return result;
  }, [layers]);

  const handleDragStart = useCallback((id: string) => {
    setDragId(id);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent, id: string) => {
    e.preventDefault();
    setOverId(id);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent, targetId: string) => {
      e.preventDefault();
      if (!dragId || dragId === targetId) {
        setDragId(null);
        setOverId(null);
        return;
      }
      const current = [...layers];
      const fromIdx = current.findIndex((l) => l.id === dragId);
      const toIdx = current.findIndex((l) => l.id === targetId);
      if (fromIdx === -1 || toIdx === -1) return;
      const [moved] = current.splice(fromIdx, 1);
      current.splice(toIdx, 0, moved);
      reorderLayers(current);
      setDragId(null);
      setOverId(null);
    },
    [dragId, layers, reorderLayers]
  );

  const handleDragEnd = useCallback(() => {
    setDragId(null);
    setOverId(null);
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Stats header */}
      <div className="shrink-0 grid grid-cols-3 gap-px bg-slate-200/60 border-b border-slate-200/60">
        <div className="bg-white/60 px-2.5 py-2 text-center">
          <div className="text-[14px] font-semibold text-slate-800">{layers.length}</div>
          <div className="text-[9px] text-slate-400 uppercase tracking-wider">总图层</div>
        </div>
        <div className="bg-white/60 px-2.5 py-2 text-center">
          <div className="text-[14px] font-semibold text-emerald-600">{visibleCount}</div>
          <div className="text-[9px] text-slate-400 uppercase tracking-wider">可见</div>
        </div>
        <div className="bg-white/60 px-2.5 py-2 text-center">
          <div className="text-[14px] font-semibold text-slate-800">{totalFeatures}</div>
          <div className="text-[9px] text-slate-400 uppercase tracking-wider">要素</div>
        </div>
      </div>

      {/* Layer list */}
      <div className="flex-1 overflow-y-auto">
        {layers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-6">
            <div className="w-10 h-10 rounded-xl bg-slate-100 flex items-center justify-center mb-2">
              <Eye size={16} className="text-slate-300" />
            </div>
            <p className="text-[11.5px] text-slate-400">暂无图层</p>
            <p className="text-[10px] text-slate-300 mt-0.5">开始分析后图层将自动添加</p>
          </div>
        ) : (
          <div className="px-2 py-2 space-y-3">
            {groups.map((group) => (
              <div key={group.name}>
                <div className="flex items-center gap-1.5 px-2 py-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  <span className="text-[9.5px] font-medium text-slate-400 uppercase tracking-wider">
                    {GROUP_NAMES[group.name] || group.name}
                  </span>
                  <span className="text-[9px] text-slate-300">({group.layers.length})</span>
                </div>

                <div className="space-y-1">
                  {group.layers.map((layer: any) => {
                    const featureCount =
                      layer.source && typeof layer.source === 'object' && 'features' in layer.source
                        ? (layer.source as any).features?.length ?? 0
                        : null;

                    const color = layer.style?.color || '#16a34a';
                    const isHeatmap = layer.type === 'heatmap';
                    const isRaster = layer.type === 'raster';
                    const isDragging = dragId === layer.id;
                    const isDragOver = overId === layer.id;

                    return (
                      <div
                        key={layer.id}
                        draggable
                        onDragStart={() => handleDragStart(layer.id)}
                        onDragOver={(e) => handleDragOver(e, layer.id)}
                        onDrop={(e) => handleDrop(e, layer.id)}
                        onDragEnd={handleDragEnd}
                        className={`rounded-lg border px-2 py-1.5 transition-all ${
                          isDragging
                            ? 'opacity-40 border-emerald-300'
                            : isDragOver
                              ? 'border-emerald-400 bg-emerald-50/40'
                              : 'border-transparent hover:bg-slate-50/80'
                        } ${!layer.visible ? 'opacity-60' : ''}`}
                      >
                        {/* Row 1: drag handle + name + actions */}
                        <div className="flex items-center gap-1.5">
                          {/* Drag handle */}
                          <div className="cursor-grab active:cursor-grabbing text-slate-300 hover:text-slate-500 shrink-0">
                            <GripVertical size={12} />
                          </div>

                          {/* Color dot */}
                          {isRaster ? (
                            <div
                              className="w-2 h-2 rounded-[2px] shrink-0"
                              style={{ backgroundColor: color }}
                            />
                          ) : isHeatmap ? (
                            <div
                              className="w-2 h-2 rounded-full shrink-0"
                              style={{ background: `radial-gradient(circle, ${color} 0%, transparent 70%)` }}
                            />
                          ) : (
                            <div
                              className="w-[7px] h-[7px] rounded-full shrink-0"
                              style={{ backgroundColor: color }}
                            />
                          )}

                          {/* Layer name */}
                          <span className="flex-1 text-[11px] text-slate-700 truncate min-w-0">
                            {layer.name}
                          </span>

                          {/* Feature count */}
                          {featureCount > 0 && (
                            <span className="shrink-0 text-[9px] text-slate-300">
                              {featureCount}
                            </span>
                          )}

                          {/* Action buttons — always visible */}
                          <div className="flex items-center gap-0.5 shrink-0">
                            <button
                              onClick={() => toggleLayer(layer.id)}
                              className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
                              title={layer.visible ? '隐藏图层' : '显示图层'}
                            >
                              {layer.visible ? <Eye size={11} /> : <EyeOff size={11} className="text-slate-300" />}
                            </button>
                            <button
                              onClick={() => removeLayer(layer.id)}
                              className="p-1 rounded hover:bg-red-50 text-slate-400 hover:text-red-500 transition-colors"
                              title="删除图层"
                            >
                              <Trash2 size={11} />
                            </button>
                          </div>
                        </div>

                        {/* Row 2: Opacity slider */}
                        <div className="flex items-center gap-2 mt-1 pl-5">
                          <input
                            type="range"
                            min={0}
                            max={100}
                            value={Math.round((layer.opacity ?? 1) * 100)}
                            onChange={(e) =>
                              updateLayer(layer.id, { opacity: parseInt(e.target.value, 10) / 100 })
                            }
                            className="flex-1 h-1 appearance-none bg-slate-200 rounded-full cursor-pointer
                              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5
                              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-emerald-500
                              [&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:cursor-pointer"
                          />
                          <span className="text-[9px] text-slate-400 w-7 text-right tabular-nums">
                            {Math.round((layer.opacity ?? 1) * 100)}%
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default LayersTab;
