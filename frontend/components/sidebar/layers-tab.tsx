'use client';

import { useMemo, useState, useCallback } from 'react';
import clsx from 'clsx';
import { Eye, EyeOff, GripVertical, Layers as LayersIcon, LocateFixed, Palette } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { Layer } from '@/lib/types/layer';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { StatusBadge } from '@/components/shared/status-badge';
import { useLayerStatuses } from '@/lib/hooks/use-layer-statuses';
import { LAYER_STATUS_LABELS } from '@/lib/layers/layer-status';
import {
  removeLayerAndCommit,
  reorderLayersAndCommit,
  setLayerOpacityAndCommit,
  toggleLayerAndCommit,
} from '@/lib/mapspec/user-mutation';

const GROUP_NAMES: Record<string, string> = {
  analysis: '分析结果',
  base: '底图',
  reference: '参考数据',
  default: '未分组',
};

function getFeatureCount(layer: Layer): number {
  // #692：MVT 挂载的大图层 source 是 ref/瓦片形态（无内联 features），
  // 此前恒显示 0——优先读 _descriptor.feature_count（store 时算好）。
  const d = layer._descriptor;
  if (d && typeof d.feature_count === 'number' && d.feature_count > 0) {
    return d.feature_count;
  }
  const src = layer.source;
  if (src && typeof src === 'object' && 'features' in src) {
    return src.features?.length ?? 0;
  }
  return 0;
}

/** 溯源提示（title）：产物 ref + 分组语义 + 数据通道 —— 只读既有事实。 */
function provenanceTitle(layer: Layer): string {
  const parts: string[] = [layer.name];
  if (layer._refId) parts.push(`artifact: ${layer._refId}`);
  if (layer._mapspecLayerId && layer._mapspecLayerId !== layer.id) {
    parts.push(`spec layer: ${layer._mapspecLayerId}`);
  }
  if (layer._tileUrl) parts.push('通道: 矢量瓦片 (MVT)');
  else if (layer._refId) parts.push('通道: ref GeoJSON');
  parts.push(`分组: ${GROUP_NAMES[layer.group || 'default'] || layer.group || '未分组'}`);
  return parts.join('\n');
}

/** UI V3：删除图层走 ConfirmAction 两段式确认（危险操作防误触 + 防双击绕过）。 */
function DeleteLayerButton({ onDelete }: { onDelete: () => void }) {
  return <ConfirmAction label="删除图层" confirmLabel="确认删除？" onConfirm={onDelete} />;
}

export function LayersTab() {
  const layers = useHudStore((s) => s.layers);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const setActiveLeftTab = useHudStore((s) => s.setActiveLeftTab);
  // Workspace V2：状态词表（loading|ready|rendering|hidden|stale|failed|
  // expired）从 MapSpec revision + artifact/ref 状态 + 最新渲染观察派生 ——
  // 只读投影，不进 store、不进 MapSpec（无并行真相）。
  const statuses = useLayerStatuses(layers);

  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  // FE-03: in-flight opacity value per layer while the slider is being dragged.
  // The range `<input>` fires onChange on every drag tick; writing to the store
  // each tick rebuilt the whole layers array → map-panel's reconcile effect
  // fired on every tick → worker spin-up + clone + layer re-add. Instead,
  // onChange only updates this local state (cheap, no store churn) and the
  // store is written once on commit (onPointerUp / onBlur) when the value has
  // actually changed.
  const [opacityDraft, setOpacityDraft] = useState<Record<string, number | undefined>>({});

  /**
   * Read the opacity percentage the slider should display: the in-flight draft
   * while dragging, otherwise the layer's committed opacity.
   */
  const sliderPercent = useCallback(
    (layer: Layer): number => {
      const draft = opacityDraft[layer.id];
      return draft !== undefined ? draft : Math.round((layer.opacity ?? 1) * 100);
    },
    [opacityDraft]
  );

  const handleOpacityChange = useCallback(
    (layer: Layer, pct: number) => {
      // Only local state here — do NOT touch the store per tick.
      setOpacityDraft((prev) => ({ ...prev, [layer.id]: pct }));
    },
    []
  );

  const commitOpacity = useCallback(
    (layer: Layer) => {
      const pct = opacityDraft[layer.id];
      if (pct === undefined) return; // nothing drafted (no drag happened)
      // Store 写入必须在 updater 之外：React 在渲染阶段执行 updater（要求
      // 纯函数），在里面写 store 会触发 "Cannot update a component while
      // rendering a different component"。
      setOpacityDraft((prev) => {
        if (!(layer.id in prev)) return prev; // onPointerUp 后紧跟 onBlur 的重复提交
        const nextDraft = { ...prev };
        delete nextDraft[layer.id];
        return nextDraft;
      });
      const next = pct / 100;
      const current = layer.opacity ?? 1;
      // Only write when the committed value actually differs — avoids a
      // redundant store update (and reconcile) on grab-without-drag.
      if (Math.abs(next - current) > 1e-9) {
        updateLayer(layer.id, { opacity: next });
        void setLayerOpacityAndCommit(layer.id, next);
      }
    },
    [opacityDraft, updateLayer]
  );

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
      if (fromIdx === -1 || toIdx === -1) {
        setDragId(null);
        setOverId(null);
        return;
      }
      const [moved] = current.splice(fromIdx, 1);
      current.splice(toIdx, 0, moved);
      void reorderLayersAndCommit(current);
      setDragId(null);
      setOverId(null);
    },
    [dragId, layers]
  );

  const handleDragEnd = useCallback(() => {
    setDragId(null);
    setOverId(null);
  }, []);

  /**
   * a11y：键盘重排。此前排序只有 draggable，鼠标独占 —— 键盘用户无法改变图层
   * 叠放次序，而叠放次序是图层面板的核心功能。Alt+↑/↓ 沿用桌面 GIS 的习惯键。
   */
  const moveLayer = useCallback(
    (id: string, delta: -1 | 1) => {
      const current = [...layers];
      const fromIdx = current.findIndex((l) => l.id === id);
      const toIdx = fromIdx + delta;
      if (fromIdx === -1 || toIdx < 0 || toIdx >= current.length) return;
      const [moved] = current.splice(fromIdx, 1);
      current.splice(toIdx, 0, moved);
      void reorderLayersAndCommit(current);
    },
    [layers]
  );

  return (
    <div className="flex flex-col h-full">
      {/* Stats header — 单行、三段。V4 之前是 53px 高的三栏卡片，在 650px 的
          面板里等于吃掉两行图层；同时「可见」数字被涂成 accent 绿，把一个中性
          计数伪装成状态。accent 现在只留给交互重点。 */}
      <div className="flex shrink-0 items-center gap-3 border-b border-edge-subtle bg-surface-panel px-panel py-1">
        {[
          { label: '总图层', value: layers.length },
          { label: '可见', value: visibleCount },
          { label: '要素', value: totalFeatures },
        ].map((stat) => (
          <div key={stat.label} className="flex items-baseline gap-1">
            <span className="text-body font-semibold tabular-nums text-ink">{stat.value}</span>
            <span className="text-micro text-ink-muted">{stat.label}</span>
          </div>
        ))}
      </div>

      {/* Layer list */}
      <div className="flex-1 overflow-y-auto">
        {layers.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              icon={LayersIcon}
              title="暂无图层"
              description="开始分析后图层将自动添加；也可以从数据织网加载数据集"
              action={{ label: '前往数据源', onClick: () => setActiveLeftTab('data_sources') }}
            />
          </div>
        ) : (
          <div className="py-1">
            {groups.map((group) => (
              <div key={group.name} className="mb-1">
                {/* 分组抬头：去掉装饰性 accent 圆点（accent 只表交互），
                    统一走 .eyebrow —— 审计里 10px 一档曾有 5 种写法。 */}
                <div className="flex items-center gap-1.5 px-panel py-1">
                  <span className="eyebrow">{GROUP_NAMES[group.name] || group.name}</span>
                  <span className="text-micro tabular-nums text-ink-disabled">
                    {group.layers.length}
                  </span>
                </div>

                <div>
                  {group.layers.map((layer) => {
                    const featureCount = getFeatureCount(layer);
                    const color = layer.style?.color || 'var(--accent-vivid)';
                    const isHeatmap = layer.type === 'heatmap';
                    const isRaster = layer.type === 'raster';
                    const isDragging = dragId === layer.id;
                    const isDragOver = overId === layer.id;
                    const globalIdx = layers.findIndex((l) => l.id === layer.id);

                    return (
                      /* 单行图层项。V4 之前每项 59–63px（两行 + 两颗 28px 操作
                         按钮撑高，图标本身只有 12px），650px 面板只放得下 8–9
                         行；QGIS 图层树是 22–24px。现在收进一行 ~26px，可见行数
                         提升到 20+ —— 密集数据面板优先可读的行数。 */
                      /* 行本身不是 tab stop：role="group" 加 tabIndex 既是角色
                         误用，也会让 20 个图层产生 20 个多余的停靠点。重排键改挂
                         在把手按钮上 —— 它本来就是"抓着移动"的那个控件。 */
                      <div
                        key={layer.id}
                        draggable
                        onDragStart={() => handleDragStart(layer.id)}
                        onDragOver={(e) => handleDragOver(e, layer.id)}
                        onDrop={(e) => handleDrop(e, layer.id)}
                        onDragEnd={handleDragEnd}
                        className={clsx(
                          'group flex min-h-row-md items-center gap-1.5 border-l-2 px-panel py-0.5 transition-colors',
                          isDragOver
                            ? 'border-l-status-accent-vivid bg-surface-selected'
                            : isDragging
                              ? 'border-l-status-accent-border opacity-40'
                              : 'border-l-transparent hover:bg-surface-hover',
                          !layer.visible && 'opacity-60'
                        )}
                      >
                        <button
                          type="button"
                          aria-label={`重新排序 ${layer.name}（第 ${globalIdx + 1} / ${layers.length} 层，Alt+↑/↓ 移动）`}
                          title="拖拽移动，或 Alt+↑/↓"
                          className="flex h-control-sm w-icon-md shrink-0 cursor-grab items-center justify-center rounded-xs text-ink-disabled transition-colors hover:text-ink-secondary active:cursor-grabbing"
                          onKeyDown={(e) => {
                            // #743: behavior must match the documented
                            // affordance (Alt+↑/↓) — bare arrows surprise-
                            // reordered while screen-reader users following
                            // the label got a no-op.
                            if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && e.altKey) {
                              e.preventDefault();
                              moveLayer(layer.id, e.key === 'ArrowUp' ? -1 : 1);
                            }
                          }}
                        >
                          <GripVertical aria-hidden size={12} />
                        </button>

                        {/* Layer symbol swatch */}
                        {isRaster ? (
                          <span aria-hidden className="h-2 w-2 shrink-0 rounded-xs" style={{ backgroundColor: color }} />
                        ) : isHeatmap ? (
                          <span
                            aria-hidden
                            className="h-2 w-2 shrink-0 rounded-pill"
                            style={{ background: `radial-gradient(circle, ${color} 0%, transparent 70%)` }}
                          />
                        ) : (
                          <span aria-hidden className="h-2 w-2 shrink-0 rounded-pill" style={{ backgroundColor: color }} />
                        )}

                        <span
                          className="min-w-0 flex-1 truncate text-body text-ink"
                          title={provenanceTitle(layer)}
                        >
                          {layer.name}
                        </span>

                        {/* 状态徽标：ready 是健康常态，不占行宽（20 个绿徽标
                            是噪声）；其余六态一望即知（含 layer.stale 的
                            「待同步」——desired 在场而 runtime 分歧，交给
                            runtime repair，不是数据问题）。 */}
                        {statuses[layer.id] && statuses[layer.id] !== 'ready' && (
                          <StatusBadge
                            status={statuses[layer.id]}
                            label={LAYER_STATUS_LABELS[statuses[layer.id]]}
                          />
                        )}

                        {featureCount > 0 && (
                          <span className="shrink-0 text-micro tabular-nums text-ink-muted">
                            {featureCount}
                          </span>
                        )}

                        {/* 不透明度滑杆内联进同一行 —— 功能不变，不再多占一行。
                            U-6（#888）：onKeyUp 补键盘提交——此前仅 onPointerUp/onBlur，
                            键盘用户调滑杆地图纹丝不动直到失焦（与样式面板实现漂移）。 */}
                        <input
                          type="range"
                          min={0}
                          max={100}
                          aria-label={`${layer.name} 不透明度`}
                          value={sliderPercent(layer)}
                          onChange={(e) => handleOpacityChange(layer, parseInt(e.target.value, 10))}
                          onPointerUp={() => commitOpacity(layer)}
                          onKeyUp={() => commitOpacity(layer)}
                          onBlur={() => commitOpacity(layer)}
                          title={`不透明度 ${sliderPercent(layer)}%`}
                          /* w-16 + .slider-track：w-9(36px) 配上被 appearance-none
                             抹掉的原生 thumb，等于一条看不见把手的 4px 细杠，
                             36px 上分 100 档也无法瞄准。 */
                          className="slider-track h-1 w-16 shrink-0"
                        />

                        <div className="flex shrink-0 items-center">
                          {/* U-1（#883）：样式编辑入口 —— LayerStylePanel 完整实现
                              341 行却因历史重构丢失了唯一的生产入口（按钮），
                              ContextPanel 承诺的「样式」实际不可达。 */}
                          <IconButton
                            size="sm"
                            label={`编辑图层样式 ${layer.name}`}
                            icon={Palette}
                            onClick={() => useHudStore.getState().setEditingLayerId(layer.id)}
                          />
                          {/* 缩放到图层：触发 map-panel 的 focusLayer 效果（fitBounds
                              到图层 bbox）。无 bbox/无几何的图层点击后无反应是
                              预期行为（计算不出范围）。 */}
                          <IconButton
                            size="sm"
                            label={`缩放到图层 ${layer.name}`}
                            icon={LocateFixed}
                            onClick={() => useHudStore.getState().focusLayer(layer.id)}
                          />
                          <IconButton
                            size="sm"
                            label={layer.visible ? '隐藏图层' : '显示图层'}
                            icon={layer.visible ? Eye : EyeOff}
                            active={layer.visible}
                            onClick={() => {
                              void toggleLayerAndCommit(layer.id);
                            }}
                          />
                          <DeleteLayerButton onDelete={() => { void removeLayerAndCommit(layer.id); }} />
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
