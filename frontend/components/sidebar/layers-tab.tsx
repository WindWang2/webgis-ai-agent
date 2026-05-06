'use client';

import { useMemo, useState, useCallback } from 'react';
import { Eye, EyeOff, Trash2, GripVertical } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { Layer } from '@/lib/types/layer';

const GROUP_NAMES: Record<string, string> = {
  analysis: '分析结果',
  base: '底图',
  reference: '参考数据',
  default: '未分组',
};

function getFeatureCount(layer: Layer): number {
  const src = layer.source;
  if (src && typeof src === 'object' && 'features' in src) {
    return src.features?.length ?? 0;
  }
  return 0;
}

export function LayersTab() {
  const layers = useHudStore((s) => s.layers);
  const toggleLayer = useHudStore((s) => s.toggleLayer);
  const removeLayer = useHudStore((s) => s.removeLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const reorderLayers = useHudStore((s) => s.reorderLayers);
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';

  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const visibleCount = useMemo(
    () => layers.filter((l) => l.visible).length,
    [layers]
  );

  const totalFeatures = useMemo(
    () => layers.reduce((sum, l) => sum + getFeatureCount(l), 0),
    [layers]
  );

  // Group layers
  const groups = useMemo(() => {
    const groupMap = new Map<string, Layer[]>();
    layers.forEach((layer) => {
      const key = layer.group || 'default';
      if (!groupMap.has(key)) groupMap.set(key, []);
      groupMap.get(key)!.push(layer);
    });
    const result: { name: string; layers: Layer[] }[] = [];
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
      <div className="shrink-0 grid grid-cols-3 gap-px" style={{ backgroundColor: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)', borderBottomColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.6)' }}>
        <div className="px-2.5 py-2 text-center" style={{ backgroundColor: isDark ? 'rgba(30,41,59,0.6)' : 'rgba(255,255,255,0.6)' }}>
          <div className="text-[14px] font-semibold" style={{ color: isDark ? '#e2e8f0' : '#1e293b' }}>{layers.length}</div>
          <div className="text-[9px] uppercase tracking-wider" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>总图层</div>
        </div>
        <div className="px-2.5 py-2 text-center" style={{ backgroundColor: isDark ? 'rgba(30,41,59,0.6)' : 'rgba(255,255,255,0.6)' }}>
          <div className="text-[14px] font-semibold" style={{ color: isDark ? '#4ade80' : '#059669' }}>{visibleCount}</div>
          <div className="text-[9px] uppercase tracking-wider" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>可见</div>
        </div>
        <div className="px-2.5 py-2 text-center" style={{ backgroundColor: isDark ? 'rgba(30,41,59,0.6)' : 'rgba(255,255,255,0.6)' }}>
          <div className="text-[14px] font-semibold" style={{ color: isDark ? '#e2e8f0' : '#1e293b' }}>{totalFeatures}</div>
          <div className="text-[9px] uppercase tracking-wider" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>要素</div>
        </div>
      </div>

      {/* Layer list */}
      <div className="flex-1 overflow-y-auto">
        {layers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-6">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-2" style={{ backgroundColor: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)' }}>
              <Eye size={16} style={{ color: isDark ? '#475569' : '#cbd5e1' }} />
            </div>
            <p className="text-[11.5px]" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>暂无图层</p>
            <p className="text-[10px] mt-0.5" style={{ color: isDark ? '#475569' : '#cbd5e1' }}>开始分析后图层将自动添加</p>
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
                  {group.layers.map((layer) => {
                    const featureCount = getFeatureCount(layer);

                    const color = layer.style?.color || '#16a34a';
                    const isHeatmap = layer.type === 'heatmap';
                    const isRaster = layer.type === 'raster';
                    const isDragging = dragId === layer.id;
                    const isDragOver = overId === layer.id;

                    let borderColor = 'transparent';
                    let bgColor = 'transparent';
                    if (isDragging) {
                      borderColor = isDark ? 'rgba(74,222,128,0.4)' : 'rgba(52,211,153,0.5)';
                      bgColor = 'transparent';
                    } else if (isDragOver) {
                      borderColor = isDark ? 'rgba(74,222,128,0.6)' : 'rgba(16,185,129,0.7)';
                      bgColor = isDark ? 'rgba(74,222,128,0.15)' : 'rgba(16,185,129,0.12)';
                    }

                    return (
                      <div
                        key={layer.id}
                        draggable
                        onDragStart={() => handleDragStart(layer.id)}
                        onDragOver={(e) => handleDragOver(e, layer.id)}
                        onDrop={(e) => handleDrop(e, layer.id)}
                        onDragEnd={handleDragEnd}
                        style={{
                          borderRadius: 8,
                          borderWidth: 1,
                          borderStyle: 'solid',
                          borderColor,
                          backgroundColor: bgColor,
                          padding: '6px 8px',
                          transition: 'all 0.15s ease',
                          opacity: !layer.visible ? 0.6 : isDragging ? 0.4 : 1,
                          cursor: isDragging ? 'grabbing' : 'default'
                        }}
                        onMouseEnter={(e) => {
                          if (!isDragging && !isDragOver) {
                            e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.1)' : 'rgba(248,250,252,0.8)';
                          }
                        }}
                        onMouseLeave={(e) => {
                          if (!isDragging && !isDragOver) {
                            e.currentTarget.style.backgroundColor = bgColor;
                          }
                        }}
                      >
                        {/* Row 1: drag handle + name + actions */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          {/* Drag handle */}
                          <div style={{ cursor: 'grab', color: isDark ? '#475569' : '#cbd5e1', flexShrink: 0 }}
                            onMouseDown={(e) => (e.currentTarget.style.cursor = 'grabbing')}
                            onMouseUp={(e) => (e.currentTarget.style.cursor = 'grab')}
                          >
                            <GripVertical size={12} />
                          </div>

                          {/* Color dot */}
                          {isRaster ? (
                            <div style={{ width: 8, height: 8, borderRadius: 2, backgroundColor: color, flexShrink: 0 }} />
                          ) : isHeatmap ? (
                            <div style={{ width: 8, height: 8, borderRadius: '50%', background: `radial-gradient(circle, ${color} 0%, transparent 70%)`, flexShrink: 0 }} />
                          ) : (
                            <div style={{ width: 7, height: 7, borderRadius: '50%', backgroundColor: color, flexShrink: 0 }} />
                          )}

                          {/* Layer name */}
                          <span style={{ flex: 1, fontSize: 11, color: isDark ? '#e2e8f0' : '#334155', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>
                            {layer.name}
                          </span>

                          {/* Feature count */}
                          {featureCount > 0 && (
                            <span style={{ flexShrink: 0, fontSize: 9, color: isDark ? '#475569' : '#cbd5e1' }}>
                              {featureCount}
                            </span>
                          )}

                          {/* Action buttons — always visible */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
                            <button
                              onClick={() => toggleLayer(layer.id)}
                              style={{ padding: 4, borderRadius: 4, border: 'none', backgroundColor: 'transparent', cursor: 'pointer', color: isDark ? '#64748b' : '#94a3b8' }}
                              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; e.currentTarget.style.color = isDark ? '#e2e8f0' : '#475569'; }}
                              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#64748b' : '#94a3b8'; }}
                              title={layer.visible ? '隐藏图层' : '显示图层'}
                            >
                              {layer.visible ? <Eye size={11} /> : <EyeOff size={11} />}
                            </button>
                            <button
                              onClick={() => removeLayer(layer.id)}
                              style={{ padding: 4, borderRadius: 4, border: 'none', backgroundColor: 'transparent', cursor: 'pointer', color: isDark ? '#64748b' : '#94a3b8' }}
                              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(248,113,113,0.15)' : 'rgba(254,226,226,0.6)'; e.currentTarget.style.color = isDark ? '#fca5a5' : '#ef4444'; }}
                              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#64748b' : '#94a3b8'; }}
                              title="删除图层"
                            >
                              <Trash2 size={11} />
                            </button>
                          </div>
                        </div>

                        {/* Row 2: Opacity slider */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, paddingLeft: 20 }}>
                          <input
                            type="range"
                            min={0}
                            max={100}
                            value={Math.round((layer.opacity ?? 1) * 100)}
                            onChange={(e) =>
                              updateLayer(layer.id, { opacity: parseInt(e.target.value, 10) / 100 })
                            }
                            style={{
                              flex: 1, height: 4, appearance: 'none' as any,
                              backgroundColor: isDark ? 'rgba(148,163,184,0.3)' : 'rgba(226,232,240,0.8)',
                              borderRadius: 999, cursor: 'pointer',
                              WebkitAppearance: 'none'
                            }}
                          />
                          <span style={{ fontSize: 9, color: isDark ? '#64748b' : '#94a3b8', width: 28, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
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
