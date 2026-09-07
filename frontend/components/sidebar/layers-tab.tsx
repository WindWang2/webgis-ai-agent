'use client';

/**
 * LayersTab — Professional Layer Workspace（Workbench V4 / Goal C Wave 2）。
 *
 * 升级自 V2 图层列表（3 个静态语义组 + 平铺行）。V4 结构：
 *   - 分组树：用户分组（可折叠/重命名/删除，workbenchSlice.layerGroups）
 *     + 语义区（base/reference/analysis/default，挂载语义不可折叠）；
 *   - 行能力：显隐/不透明度/排序/锁定/隔离/样式/复制粘贴样式/重试/缩放/
 *     删除 —— 全部复用既有 mutation 通道（user-mutation CAS 串行链）；
 *   - 批量操作：多选（selection 真相在 workbenchSlice.selectedLayerIds）、
 *     批量显隐/不透明度/移入分组/删除（layer-ops，锁定层自动跳过）；
 *   - 搜索：SearchField 子串过滤（id/name/ref）。
 *
 * 状态纪律（ADR-0104）：分组/选择/锁定/隔离是 UI projection（workbenchSlice，
 * 会话级不持久化）；地图语义真相仍只在 MapSpec / backend contract。
 */
import { useMemo, useState, useCallback, useEffect, useSyncExternalStore } from 'react';
import clsx from 'clsx';
import {
  Eye, EyeOff, GripVertical, Layers as LayersIcon, LocateFixed, Palette,
  Lock, LockOpen, Crosshair, Copy, ClipboardPaste, RotateCw, FolderPlus,
  ChevronDown, ChevronRight, CheckSquare, Square, Trash2, MoreHorizontal, Group,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { Layer, LayerStyle } from '@/lib/types/layer';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { StatusBadge } from '@/components/shared/status-badge';
import { SearchField } from '@/components/shared/search-field';
import { useLayerStatuses } from '@/lib/hooks/use-layer-statuses';
import {
  getFilterEvidence,
  getFilterEvidenceGeneration,
  subscribeFilterEvidence,
} from '@/lib/layers/filter-evidence';
import { LAYER_STATUS_LABELS } from '@/lib/layers/layer-status';
import { projectWorkspace, semanticGroupLabel, type WorkspaceRow, type WorkspaceSection } from '@/lib/layers/workspace-projection';
import {
  isolateLayerAndCommit,
  clearIsolateAndCommit,
  batchSetVisibility,
  batchSetOpacity,
  pasteStyle,
  retryLayerLoad,
} from '@/lib/layers/layer-ops';
import {
  removeLayerAndCommit,
  reorderLayersAndCommit,
  setLayerOpacityAndCommit,
  toggleLayerAndCommit,
} from '@/lib/mapspec/user-mutation';

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

/** 溯源提示（title）：产物 ref + 分组语义 + 数据通道 + 展示归属 —— 只读既有事实。 */
function provenanceTitle(layer: Layer): string {
  const parts: string[] = [layer.name];
  if (layer._refId) parts.push(`artifact: ${layer._refId}`);
  if (layer._mapspecLayerId && layer._mapspecLayerId !== layer.id) {
    parts.push(`spec layer: ${layer._mapspecLayerId}`);
  }
  if (layer._tileUrl) parts.push('通道: 矢量瓦片 (MVT)');
  else if (layer._refId) parts.push('通道: ref GeoJSON');
  parts.push(`语义组: ${semanticGroupLabel(layer.group || 'default')}`);
  if (layer._userPinned) parts.push('用户已固定（agent 收口不隐藏）');
  if (typeof layer._displayTurn === 'number') parts.push(`展示轮次: ${layer._displayTurn}`);
  return parts.join('\n');
}

/** UI V3：删除图层走 ConfirmAction 两段式确认（危险操作防误触 + 防双击绕过）。 */
function DeleteLayerButton({ onDelete, disabled }: { onDelete: () => void; disabled?: boolean }) {
  return <ConfirmAction label="删除图层" confirmLabel="确认删除？" onConfirm={onDelete} disabled={disabled} />;
}

interface FilterBadgeView {
  label: string;
  title: string;
  tone: 'warn' | 'info';
}

/**
 * Runtime V4（§14）：把 LayerFilterEvidence 投影为行内徽标视图。
 * - empty / invalid → warn 色（内容被过滤清空 / 过滤字段不存在）；
 * - active → 轻量命中数（有 matched_count 时）；
 * - inactive / unknown / stale → 无徽标（未知 ≠ 异常，不为噪声占行宽）。
 */
function useFilterEvidenceBadges(layers: Layer[]): Record<string, FilterBadgeView> {
  const generation = useSyncExternalStore(subscribeFilterEvidence, getFilterEvidenceGeneration);
  return useMemo(() => {
    const out: Record<string, FilterBadgeView> = {};
    for (const layer of layers) {
      const evidence = getFilterEvidence(layer.id);
      if (!evidence) continue;
      if (evidence.status === 'empty') {
        out[layer.id] = {
          label: '过滤后 0 要素',
          title: '当前过滤条件没有命中任何要素（检查图例区间/选择过滤/字段拼写）',
          tone: 'warn',
        };
      } else if (evidence.status === 'invalid') {
        out[layer.id] = {
          label: '过滤字段不存在',
          title: '过滤引用的字段在该层要素属性中不存在',
          tone: 'warn',
        };
      } else if (evidence.status === 'active' && evidence.matched_count != null) {
        out[layer.id] = {
          label: `过滤 ${evidence.matched_count}`,
          title: `过滤命中 ${evidence.matched_count} 要素（扫描 ${evidence.scanned ?? '?'}）`,
          tone: 'info',
        };
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generation is the change signal
  }, [layers, generation]);
}

/* ─────────────────────────── 分组抬头 ─────────────────────────── */

function GroupHeader({
  section,
  isUserGroup,
  layerCount,
  onDropOnGroup,
  onDragOverGroup,
  isDropTarget,
}: {
  section: WorkspaceSection;
  isUserGroup: boolean;
  layerCount: number;
  onDropOnGroup: (groupId: string | null) => void;
  onDragOverGroup: (groupId: string | null) => void;
  isDropTarget: boolean;
}) {
  const toggleGroupCollapsed = useHudStore((s) => s.toggleGroupCollapsed);
  const renameLayerGroup = useHudStore((s) => s.renameLayerGroup);
  const removeLayerGroup = useHudStore((s) => s.removeLayerGroup);
  const assignLayersToGroup = useHudStore((s) => s.assignLayersToGroup);
  const selectedLayerIds = useHudStore((s) => s.selectedLayerIds);
  const layers = useHudStore((s) => s.layers);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(section.name);

  const memberIds = useMemo(
    () => section.rows.map((row) => row.layer.id),
    [section.rows],
  );
  const allVisible = useMemo(() => {
    if (memberIds.length === 0) return false;
    return memberIds.every((id) => layers.find((l) => l.id === id)?.visible !== false);
  }, [memberIds, layers]);

  const toggleGroupVisibility = useCallback(() => {
    void batchSetVisibility(memberIds, !allVisible);
  }, [memberIds, allVisible]);

  const commitRename = () => {
    const name = draftName.trim();
    if (name && name !== section.name && section.id) renameLayerGroup(section.id, name);
    setRenaming(false);
  };

  return (
    <div
      className={clsx(
        'flex items-center gap-1 px-panel py-1',
        isDropTarget && 'bg-surface-selected outline outline-1 outline-dashed outline-status-accent-border',
      )}
      data-testid={`group-header-${section.id ?? section.name}`}
      onDragOver={(e) => {
        if (isUserGroup || section.id === null) {
          e.preventDefault();
          onDragOverGroup(section.id);
        }
      }}
      onDrop={(e) => {
        if (!isUserGroup && section.id !== null) return;
        e.preventDefault();
        e.stopPropagation();
        onDropOnGroup(section.id);
      }}
    >
      {isUserGroup ? (
        <button
          type="button"
          aria-label={`${section.collapsed ? '展开' : '折叠'}分组 ${section.name}`}
          aria-expanded={!section.collapsed}
          className="flex h-control-sm w-control-sm items-center justify-center rounded-xs text-ink-muted hover:bg-surface-hover hover:text-ink"
          onClick={() => section.id && toggleGroupCollapsed(section.id)}
        >
          {section.collapsed ? <ChevronRight aria-hidden size={12} /> : <ChevronDown aria-hidden size={12} />}
        </button>
      ) : (
        <span className="w-control-sm" aria-hidden />
      )}

      {renaming && section.id ? (
        <input
          autoFocus
          value={draftName}
          aria-label="重命名分组"
          onChange={(e) => setDraftName(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename();
            if (e.key === 'Escape') setRenaming(false);
          }}
          className="h-control-sm w-32 rounded-xs border border-status-accent-border bg-surface-sunken px-1 text-micro text-ink focus:outline-none"
        />
      ) : (
        <button
          type="button"
          disabled={!isUserGroup}
          title={isUserGroup ? '双击重命名分组' : semanticGroupLabel(section.name) !== section.name ? undefined : '语义分组（挂载语义，不可重命名）'}
          onDoubleClick={() => {
            if (!isUserGroup || !section.id) return;
            setDraftName(section.name);
            setRenaming(true);
          }}
          className={clsx(
            'eyebrow truncate',
            isUserGroup && 'cursor-text',
          )}
        >
          {isUserGroup ? section.name : semanticGroupLabel(section.name)}
        </button>
      )}
      <span className="text-micro tabular-nums text-ink-disabled">{layerCount}</span>

      <div className="ml-auto flex items-center">
        {isUserGroup && section.id && (
          <>
            <IconButton
              size="sm"
              label={`将选中图层移入分组 ${section.name}`}
              icon={Group}
              disabled={selectedLayerIds.length === 0}
              onClick={() => assignLayersToGroup(selectedLayerIds, section.id)}
            />
            <ConfirmAction
              label={`删除分组 ${section.name}（图层保留）`}
              confirmLabel="确认删除分组？"
              onConfirm={() => section.id && removeLayerGroup(section.id)}
            />
          </>
        )}
        {memberIds.length > 0 && (
          <IconButton
            size="sm"
            label={allVisible ? `隐藏分组 ${section.name} 全部图层` : `显示分组 ${section.name} 全部图层`}
            icon={allVisible ? Eye : EyeOff}
            active={allVisible}
            onClick={toggleGroupVisibility}
          />
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────── 图层行 ─────────────────────────── */

function LayerRow({
  row,
  globalIdx,
  totalCount,
  isDragging,
  isDragOver,
  isolated,
  onDragStart,
  onDragOverRow,
  onDropOnRow,
  onDragEnd,
  onMove,
  styleClipboard,
  setStyleClipboard,
}: {
  row: WorkspaceRow;
  globalIdx: number;
  totalCount: number;
  isDragging: boolean;
  isDragOver: boolean;
  isolated: boolean;
  onDragStart: (id: string) => void;
  onDragOverRow: (e: React.DragEvent, id: string) => void;
  onDropOnRow: (e: React.DragEvent, id: string) => void;
  onDragEnd: () => void;
  onMove: (id: string, delta: -1 | 1) => void;
  styleClipboard: LayerStyle | null;
  setStyleClipboard: (style: LayerStyle | null) => void;
}) {
  const layer = row.layer;
  const selected = row.selected;
  const locked = row.locked;
  const statuses = useLayerStatuses([layer]);
  const status = statuses[layer.id];
  const filterBadge = useFilterEvidenceBadges([layer])[layer.id];
  const toggleLayerSelected = useHudStore((s) => s.toggleLayerSelected);
  const toggleLayerLocked = useHudStore((s) => s.toggleLayerLocked);
  const isolatedActive = useHudStore((s) => s.isolatedLayerId);

  const [showMore, setShowMore] = useState(false);
  const color = layer.style?.color || 'var(--accent-vivid)';
  const isHeatmap = layer.type === 'heatmap';
  const isRaster = layer.type === 'raster';
  const featureCount = getFeatureCount(layer);

  const sliderPercent = layer.opacity !== undefined ? Math.round(layer.opacity * 100) : 100;
  const [draft, setDraft] = useState<number | null>(null);

  const commitOpacity = useCallback((final: number) => {
    const next = final / 100;
    if (Math.abs(next - (layer.opacity ?? 1)) > 1e-9) {
      void setLayerOpacityAndCommit(layer.id, next);
    }
    setDraft(null);
  }, [layer.id, layer.opacity]);

  return (
    <>
      <div
        draggable={!locked}
        onDragStart={() => onDragStart(layer.id)}
        onDragOver={(e) => onDragOverRow(e, layer.id)}
        onDrop={(e) => onDropOnRow(e, layer.id)}
        onDragEnd={onDragEnd}
        data-testid={`layer-row-${layer.id}`}
        data-locked={locked || undefined}
        className={clsx(
          'group flex min-h-row-md items-center gap-1 border-l-2 px-panel py-0.5 transition-colors',
          selected
            ? 'border-l-status-accent-vivid bg-surface-selected'
            : isDragOver
              ? 'border-l-status-accent-vivid bg-surface-selected'
              : isDragging
                ? 'border-l-status-accent-border opacity-40'
                : 'border-l-transparent hover:bg-surface-hover',
          (!layer.visible || locked) && 'opacity-70',
        )}
      >
        {/* 多选（批量操作选择真相；键盘可达） */}
        <button
          type="button"
          aria-label={selected ? `取消选择 ${layer.name}` : `选择 ${layer.name}`}
          onClick={() => toggleLayerSelected(layer.id)}
          className="flex h-control-sm w-control-sm shrink-0 items-center justify-center rounded-xs text-ink-muted hover:text-ink"
        >
          {selected ? <CheckSquare aria-hidden size={13} /> : <Square aria-hidden size={13} />}
        </button>

        <button
          type="button"
          aria-label={`重新排序 ${layer.name}（第 ${globalIdx + 1} / ${totalCount} 层，Alt+↑/↓ 移动）`}
          title="拖拽移动，或 Alt+↑/↓"
          disabled={locked}
          className="flex h-control-sm w-icon-md shrink-0 cursor-grab items-center justify-center rounded-xs text-ink-disabled transition-colors hover:text-ink-secondary active:cursor-grabbing disabled:cursor-not-allowed"
          onKeyDown={(e) => {
            // #743: behavior must match the documented affordance (Alt+↑/↓).
            if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && e.altKey) {
              e.preventDefault();
              onMove(layer.id, e.key === 'ArrowUp' ? -1 : 1);
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

        {/* 状态徽标：ready 是健康常态，不占行宽；其余六态一望即知。 */}
        {status && status !== 'ready' && (
          <StatusBadge status={status} label={LAYER_STATUS_LABELS[status]} />
        )}

        {/* Runtime V4（§14）：过滤命中证据徽标。 */}
        {filterBadge && (
          <span
            className={`shrink-0 rounded-xs px-1 text-micro tabular-nums ${
              filterBadge.tone === 'warn'
                ? 'bg-status-critical-soft text-status-critical'
                : 'bg-surface-subtle text-ink-muted'
            }`}
            title={filterBadge.title}
            data-testid={`filter-evidence-${layer.id}`}
          >
            {filterBadge.label}
          </span>
        )}

        {isolatedActive === layer.id && (
          <span className="shrink-0 rounded-xs bg-status-accent-soft px-1 text-micro text-status-accent" title="该图层处于隔离显示（solo）">
            隔离
          </span>
        )}

        {featureCount > 0 && (
          <span className="shrink-0 text-micro tabular-nums text-ink-muted">
            {featureCount}
          </span>
        )}

        {/* 不透明度滑杆：拖动只写本地 draft，pointerUp/blur/keyup 提交一次。 */}
        <input
          type="range"
          min={0}
          max={100}
          aria-label={`${layer.name} 不透明度`}
          value={draft ?? sliderPercent}
          onChange={(e) => setDraft(parseInt(e.target.value, 10))}
          onPointerUp={() => draft != null && commitOpacity(draft)}
          onKeyUp={() => draft != null && commitOpacity(draft)}
          onBlur={() => draft != null && commitOpacity(draft)}
          title={`不透明度 ${draft ?? sliderPercent}%`}
          className="slider-track h-1 w-16 shrink-0"
        />

        <div className="flex shrink-0 items-center">
          <IconButton
            size="sm"
            label={`编辑图层样式 ${layer.name}`}
            icon={Palette}
            disabled={locked}
            onClick={() => useHudStore.getState().setEditingLayerId(layer.id)}
          />
          <IconButton
            size="sm"
            label={locked ? `解锁图层 ${layer.name}` : `锁定图层 ${layer.name}`}
            icon={locked ? Lock : LockOpen}
            active={locked}
            onClick={() => toggleLayerLocked(layer.id)}
          />
          <IconButton
            size="sm"
            label={`缩放到图层 ${layer.name}`}
            icon={LocateFixed}
            disabled={locked}
            onClick={() => useHudStore.getState().focusLayer(layer.id)}
          />
          <IconButton
            size="sm"
            label={layer.visible ? '隐藏图层' : '显示图层'}
            icon={layer.visible ? Eye : EyeOff}
            active={layer.visible}
            disabled={locked}
            onClick={() => {
              void toggleLayerAndCommit(layer.id);
            }}
          />
          <IconButton
            size="sm"
            label={`更多操作 ${layer.name}`}
            icon={MoreHorizontal}
            active={showMore}
            aria-expanded={showMore}
            onClick={() => setShowMore((v) => !v)}
          />
          <DeleteLayerButton onDelete={() => { void removeLayerAndCommit(layer.id); }} disabled={locked} />
        </div>
      </div>

      {/* 展开的次要操作行（隔离/样式复制/粘贴/重试/移出分组）——键盘可达的
          「菜单」等价物，不引入浮层焦点管理。 */}
      {showMore && (
        <div
          data-testid={`layer-more-${layer.id}`}
          className="flex flex-wrap items-center gap-1 border-b border-edge-subtle bg-surface-subtle px-panel py-1"
        >
          <IconButton
            size="sm"
            label={isolated ? '退出隔离显示' : `隔离显示 ${layer.name}（其余图层隐藏）`}
            icon={Crosshair}
            active={isolated}
            onClick={() => {
              if (isolated) void clearIsolateAndCommit();
              else void isolateLayerAndCommit(layer.id);
            }}
          />
          <span className="text-micro text-ink-muted">隔离</span>
          <IconButton
            size="sm"
            label={`复制图层样式 ${layer.name}`}
            icon={Copy}
            onClick={() => {
              if (layer.style) setStyleClipboard({ ...layer.style });
            }}
          />
          <span className="text-micro text-ink-muted">复制样式</span>
          <IconButton
            size="sm"
            label={`粘贴样式到 ${layer.name}`}
            icon={ClipboardPaste}
            disabled={!styleClipboard || locked}
            onClick={() => {
              if (styleClipboard) void pasteStyle(styleClipboard, layer.id);
            }}
          />
          <span className="text-micro text-ink-muted">粘贴样式</span>
          {layer._refId && (
            <>
              <IconButton
                size="sm"
                label={`重新加载数据 ${layer.name}`}
                icon={RotateCw}
                onClick={() => void retryLayerLoad(layer.id)}
              />
              <span className="text-micro text-ink-muted">重载</span>
            </>
          )}
          {row.groupId && (
            <button
              type="button"
              className="rounded-xs px-1 py-0.5 text-micro text-ink-secondary hover:bg-surface-hover hover:text-ink"
              onClick={() => useHudStore.getState().assignLayersToGroup([layer.id], null)}
            >
              移出「{sectionNameOf(row.groupId)}」
            </button>
          )}
        </div>
      )}
    </>
  );
}

function sectionNameOf(groupId: string): string {
  return useHudStore.getState().layerGroups.find((g) => g.id === groupId)?.name ?? '分组';
}

/* ─────────────────────────── 批量操作条 ─────────────────────────── */

function BatchActionBar({ scopeIds }: { scopeIds: string[] }) {
  const selectedLayerIds = useHudStore((s) => s.selectedLayerIds);
  const clearLayerSelection = useHudStore((s) => s.clearLayerSelection);
  const assignLayersToGroup = useHudStore((s) => s.assignLayersToGroup);
  const createLayerGroup = useHudStore((s) => s.createLayerGroup);
  const layerGroups = useHudStore((s) => s.layerGroups);
  const [opacity, setOpacity] = useState<number | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  return (
    <div
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-edge-subtle bg-surface-subtle px-panel py-1.5"
      data-testid="layer-batch-bar"
      role="toolbar"
      aria-label="图层批量操作"
    >
      <span className="text-micro tabular-nums text-ink-secondary">
        已选 {selectedLayerIds.length}
      </span>
      <button
        type="button"
        className="rounded-xs px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-hover hover:text-ink"
        onClick={() => void batchSetVisibility(scopeIds, true)}
      >
        全部显示
      </button>
      <button
        type="button"
        className="rounded-xs px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-hover hover:text-ink"
        onClick={() => void batchSetVisibility(scopeIds, false)}
      >
        全部隐藏
      </button>
      <label className="flex items-center gap-1 text-micro text-ink-muted">
        不透明度
        <input
          type="range"
          min={0}
          max={100}
          defaultValue={100}
          aria-label="批量设置不透明度"
          className="slider-track h-1 w-16"
          onChange={(e) => setOpacity(parseInt(e.target.value, 10))}
          onPointerUp={() => opacity != null && void batchSetOpacity(scopeIds, opacity / 100)}
          onKeyUp={() => opacity != null && void batchSetOpacity(scopeIds, opacity / 100)}
        />
      </label>
      <label className="flex items-center gap-1 text-micro text-ink-muted">
        <span className="sr-only">移入分组</span>
        <Group aria-hidden size={12} />
        <select
          aria-label="将选中图层移入分组"
          defaultValue=""
          className="h-control-sm rounded-xs border border-edge-subtle bg-surface-panel px-1 text-micro text-ink"
          onChange={(e) => {
            const value = e.target.value;
            if (value === '__new__') {
              const id = createLayerGroup(`分组 ${layerGroups.length + 1}`);
              assignLayersToGroup(selectedLayerIds, id);
            } else if (value === '') {
              assignLayersToGroup(selectedLayerIds, null);
            } else {
              assignLayersToGroup(selectedLayerIds, value);
            }
            e.target.value = '';
          }}
        >
          <option value="">移入分组…</option>
          <option value="__new__">＋ 新建分组</option>
          {layerGroups.map((g) => (
            <option key={g.id} value={g.id}>{g.name}</option>
          ))}
          <option value="__ungrouped__">移出分组</option>
        </select>
      </label>
      {confirmingDelete ? (
        <span className="flex items-center gap-1 text-micro text-status-critical">
          删除 {selectedLayerIds.length} 层？
          <button
            type="button"
            className="rounded-xs bg-status-critical-soft px-1.5 py-0.5 font-medium"
            onClick={() => {
              const store = useHudStore.getState();
              for (const id of selectedLayerIds) {
                if (store.lockedLayerIds.includes(id)) continue;
                void removeLayerAndCommit(id);
              }
              clearLayerSelection();
              setConfirmingDelete(false);
            }}
          >
            确认
          </button>
          <button type="button" className="rounded-xs px-1.5 py-0.5" onClick={() => setConfirmingDelete(false)}>
            取消
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="flex items-center gap-1 rounded-xs px-1.5 py-0.5 text-micro text-status-critical hover:bg-status-critical-soft"
          onClick={() => setConfirmingDelete(true)}
        >
          <Trash2 aria-hidden size={12} /> 删除
        </button>
      )}
      <button
        type="button"
        aria-label="取消选择"
        className="ml-auto rounded-xs px-1.5 py-0.5 text-micro text-ink-muted hover:bg-surface-hover hover:text-ink"
        onClick={clearLayerSelection}
      >
        取消选择
      </button>
    </div>
  );
}

/* ─────────────────────────── 主组件 ─────────────────────────── */

export function LayersTab() {
  const layers = useHudStore((s) => s.layers);
  const setActiveLeftTab = useHudStore((s) => s.setActiveLeftTab);
  const layerGroups = useHudStore((s) => s.layerGroups);
  const layerGroupMembership = useHudStore((s) => s.layerGroupMembership);
  const lockedLayerIds = useHudStore((s) => s.lockedLayerIds);
  const selectedLayerIds = useHudStore((s) => s.selectedLayerIds);
  const createLayerGroup = useHudStore((s) => s.createLayerGroup);
  const isolatedLayerId = useHudStore((s) => s.isolatedLayerId);

  const [search, setSearch] = useState('');
  const [styleClipboard, setStyleClipboard] = useState<LayerStyle | null>(null);

  // 拖拽状态：行重排（跨组 = 重排 + 换组）与组头投放（换组）。
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [overGroupId, setOverGroupId] = useState<string | 'semantic' | null>(null);

  const projection = useMemo(
    () => projectWorkspace({
      layers,
      groups: layerGroups,
      membership: layerGroupMembership,
      lockedLayerIds,
      selectedLayerIds,
      search,
    }),
    [layers, layerGroups, layerGroupMembership, lockedLayerIds, selectedLayerIds, search],
  );

  // B10 边角：图层删除后清理残留的拖拽/搜索无关状态（锁定选择由调用方语义决定）。
  useEffect(() => {
    if (dragId && !layers.some((l) => l.id === dragId)) {
      setDragId(null);
      setOverId(null);
    }
  }, [layers, dragId]);

  const visibleCount = useMemo(() => layers.filter((l) => l.visible).length, [layers]);

  const handleDragStart = useCallback((id: string) => setDragId(id), []);

  const handleDragOverRow = useCallback((e: React.DragEvent, id: string) => {
    e.preventDefault();
    setOverId(id);
    setOverGroupId(null);
  }, []);

  const handleDropOnRow = useCallback(
    (e: React.DragEvent, targetId: string) => {
      e.preventDefault();
      setOverId(null);
      setOverGroupId(null);
      if (!dragId || dragId === targetId) {
        setDragId(null);
        return;
      }
      const current = [...layers];
      const fromIdx = current.findIndex((l) => l.id === dragId);
      const toIdx = current.findIndex((l) => l.id === targetId);
      if (fromIdx === -1 || toIdx === -1) {
        setDragId(null);
        return;
      }
      const [moved] = current.splice(fromIdx, 1);
      current.splice(toIdx, 0, moved);
      // B4（workbench-v4）：跨组行投放 = 重排 + 换组 —— 组是 UI projection，
      // 跟随投放目标行所在的用户组（语义区投放 = 移出用户组）。
      const targetGroup = layerGroupMembership[targetId] ?? null;
      const draggedGroup = layerGroupMembership[dragId] ?? null;
      if (targetGroup !== draggedGroup) {
        useHudStore.getState().assignLayersToGroup([dragId], targetGroup);
      }
      void reorderLayersAndCommit(current);
      setDragId(null);
    },
    [dragId, layers, layerGroupMembership],
  );

  const handleDropOnGroup = useCallback(
    (groupId: string | null) => {
      setOverGroupId(null);
      setOverId(null);
      if (!dragId) return;
      useHudStore.getState().assignLayersToGroup([dragId], groupId);
      setDragId(null);
    },
    [dragId],
  );

  const handleDragOverGroup = useCallback((groupId: string | null) => {
    setOverGroupId(groupId ?? 'semantic');
    setOverId(null);
  }, []);

  const handleDragEnd = useCallback(() => {
    setDragId(null);
    setOverId(null);
    setOverGroupId(null);
  }, []);

  /**
   * a11y：键盘重排（Alt+↑/↓，桌面 GIS 习惯键）。作用于全局扁平序 ——
   * 与拖拽一致，跨区移动即换区。
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

  const userGroupIds = useMemo(
    () => new Set(layerGroups.map((g) => g.id)),
    [layerGroups],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Stats header + 搜索 + 新建分组 */}
      <div className="flex shrink-0 items-center gap-3 border-b border-edge-subtle bg-surface-panel px-panel py-1">
        {[
          { label: '总图层', value: layers.length },
          { label: '可见', value: visibleCount },
        ].map((stat) => (
          <div key={stat.label} className="flex items-baseline gap-1">
            <span className="text-body font-semibold tabular-nums text-ink">{stat.value}</span>
            <span className="text-micro text-ink-muted">{stat.label}</span>
          </div>
        ))}
        <div className="ml-auto flex w-40 items-center">
          <SearchField
            value={search}
            onChange={setSearch}
            placeholder="搜索图层"
            aria-label="搜索图层（名称 / id / ref）"
          />
        </div>
        <IconButton
          size="sm"
          label="新建分组"
          icon={FolderPlus}
          onClick={() => createLayerGroup(`分组 ${layerGroups.length + 1}`)}
        />
      </div>

      {/* 批量操作条（有选择时出现） */}
      {selectedLayerIds.length > 0 && <BatchActionBar scopeIds={selectedLayerIds} />}

      {/* 隔离提示条 */}
      {isolatedLayerId && (
        <div className="flex shrink-0 items-center gap-2 border-b border-edge-subtle bg-status-accent-soft px-panel py-1 text-micro text-ink">
          <Crosshair aria-hidden size={12} />
          <span>隔离显示中 —— 其余图层已临时隐藏</span>
          <button
            type="button"
            className="ml-auto rounded-xs px-1.5 py-0.5 text-status-accent hover:bg-surface-hover"
            onClick={() => void clearIsolateAndCommit()}
          >
            退出隔离
          </button>
        </div>
      )}

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
            {projection.sections.map((section) => {
              const isUserGroup = section.id != null && userGroupIds.has(section.id);
              const globalIndexOf = (row: WorkspaceRow) =>
                layers.findIndex((l) => l.id === row.layer.id);
              return (
                <div key={section.id ?? `semantic-${section.name}`} className="mb-1">
                  <GroupHeader
                    section={section}
                    isUserGroup={isUserGroup}
                    layerCount={section.rows.length}
                    isDropTarget={overGroupId === (section.id ?? 'semantic') && dragId != null}
                    onDropOnGroup={handleDropOnGroup}
                    onDragOverGroup={handleDragOverGroup}
                  />
                  {!section.collapsed && (
                    <div>
                      {section.rows.map((row) => (
                        <LayerRow
                          key={row.layer.id}
                          row={row}
                          globalIdx={globalIndexOf(row)}
                          totalCount={layers.length}
                          isDragging={dragId === row.layer.id}
                          isDragOver={overId === row.layer.id}
                          isolated={isolatedLayerId === row.layer.id}
                          onDragStart={handleDragStart}
                          onDragOverRow={handleDragOverRow}
                          onDropOnRow={handleDropOnRow}
                          onDragEnd={handleDragEnd}
                          onMove={moveLayer}
                          styleClipboard={styleClipboard}
                          setStyleClipboard={setStyleClipboard}
                        />
                      ))}
                      {section.rows.length === 0 && (
                        <div className="px-panel py-1 text-micro text-ink-disabled">
                          {search ? '无匹配图层' : '空分组 —— 拖入或选择图层移入'}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export default LayersTab;
