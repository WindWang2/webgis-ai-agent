'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import { t as tNow } from '@/lib/i18n/t';
import {
  Eye,
  EyeOff,
  ChevronsDownUp,
  ChevronsUpDown,
  Maximize,
  ArrowUpToLine,
  LayoutDashboard,
  PanelRight,
  Copy,
  Trash2,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { StatusBadge } from '@/components/shared/status-badge';
import { useMapComponents } from '@/lib/hooks/use-map-components';
import { commitComponentPatch, commitComponentLifecycle } from '@/lib/mapspec/component-mutation';
import {
  availableActions,
  bringToFrontPatch,
  manageableComponents,
  maxFloatingZIndex,
  resetPositionPatch,
  toggleCollapsePatch,
  toggleVisibilityPatch,
} from '@/lib/map-components/manager';

/**
 * Map Components Manager（Workspace V2 / Goal C4）—— 多实例组件的统一
 * 管理入口。实例列表来自 committed MapSpec（共享 resolveMapComponents
 * 投影），全部动作经既有 patch_component CAS 通道写回**同一组件真相**：
 * 本面板不持有任何组件状态（placement 在 MapSpec、乐观覆盖在
 * component-mutation、dock 归属在工作区 dock state —— 语义与布局分离）。
 *
 * Runtime V4（§20）：生命周期动作（复制/真删除/重绑定）经
 * commitComponentLifecycle 的 remove/duplicate/rebind 意图提交 —— 与 patch
 * 同一 CAS 串行链，不开第二套 semantic state 写路径。
 */

const TYPE_LABEL_KEYS: Record<string, string> = {
  title: 'comp.type.title',
  subtitle: 'comp.type.subtitle',
  north_arrow: 'comp.type.north_arrow',
  scale_bar: 'comp.type.scale_bar',
  attribution: 'comp.type.attribution',
  legend: 'comp.type.legend',
  categorical_legend: 'comp.type.categorical_legend',
  continuous_colorbar: 'comp.type.continuous_colorbar',
  statistics_panel: 'comp.type.statistics_panel',
  chart_panel: 'comp.type.chart_panel',
  table_panel: 'comp.type.table_panel',
  annotation: 'comp.type.annotation',
  inset_map: 'comp.type.inset_map',
};

function typeLabel(type: string): string {
  // render 期被组件调用：命令式 t 读 store 当前语言（随语言切换的重渲染自动刷新）
  const key = TYPE_LABEL_KEYS[type];
  return key ? tNow(`sidebar.${key}`) : type;
}

export function ComponentsTab({ sessionId }: { sessionId?: string | null }) {
  const t = useT();
  const resolved = useMapComponents();
  const manageable = useMemo(() => manageableComponents(resolved), [resolved]);
  const maxZ = useMemo(() => maxFloatingZIndex(resolved), [resolved]);

  const dockPanel = useHudStore((s) => s.dockPanel);
  const dockPlacements = useHudStore((s) => s.dockPlacements);
  // Runtime V4：待删除确认（两段式，与图层删除同款防误触纪律）。目标组件
  // 离开 spec（被并发删除）时自动复位，确认条不留残迹。
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  useEffect(() => {
    if (confirmRemoveId && !manageable.some((c) => c.id === confirmRemoveId)) {
      setConfirmRemoveId(null);
    }
  }, [manageable, confirmRemoveId]);

  const run = useCallback(
    (componentId: string, patch: Parameters<typeof commitComponentPatch>[1]) => {
      void commitComponentPatch(componentId, patch).catch(() => {
        /* CAS 失败已在 component-mutation 内收敛到服务端真相 */
      });
    },
    [],
  );

  // review 修复：语义错误（单例复制/绑定不存在等）必须生产可见 —— 行内
  // 错误条 3s 自动消隐（CAS 冲突已在 mutation 层静默收敛，不会进这里）。
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  useEffect(() => {
    if (!lifecycleError) return;
    const t = setTimeout(() => setLifecycleError(null), 3000);
    return () => clearTimeout(t);
  }, [lifecycleError]);
  const runLifecycle = useCallback(
    (componentId: string, mutation: Parameters<typeof commitComponentLifecycle>[1]) => {
      void commitComponentLifecycle(componentId, mutation).catch((e) => {
        const msg = e instanceof Error ? e.message : String(e);
        setLifecycleError(`${mutation.action} 失败：${msg.slice(0, 120)}`);
      });
    },
    [],
  );

  if (!sessionId) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={LayoutDashboard}
          title={t('sidebar.comp.emptyNoSpecTitle')}
          description={t('sidebar.comp.emptyNoSpecDesc')}
        />
      </div>
    );
  }

  if (manageable.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={LayoutDashboard}
          title={t('sidebar.comp.emptyNoSpecTitle')}
          description={t('sidebar.comp.emptyNoInstanceDesc')}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex shrink-0 items-center gap-3 border-b border-edge-subtle bg-surface-panel px-panel py-1">
        <div className="flex items-baseline gap-1">
          <span className="text-body font-semibold tabular-nums text-ink">{manageable.length}</span>
          <span className="text-micro text-ink-muted">{t('sidebar.comp.unit')}</span>
        </div>
        <div className="flex items-baseline gap-1">
          <span className="text-body font-semibold tabular-nums text-ink">
            {manageable.filter((c) => c.enabled).length}
          </span>
          <span className="text-micro text-ink-muted">{t('sidebar.comp.enabledUnit')}</span>
        </div>
      </div>

      {lifecycleError && (
        <div
          role="alert"
          data-testid="component-lifecycle-error"
          className="mx-2 my-1 rounded-xs bg-status-critical-soft px-2 py-1 text-micro text-status-critical"
        >
          {lifecycleError}
        </div>
      )}

      <div className="flex-1 overflow-y-auto py-1" role="list">
        {manageable.map((c) => {
          const actions = availableActions(c);
          const binding = c.layerId ? `@ ${c.layerId}` : '';
          const confirming = confirmRemoveId === c.id;
          return (
            <div
              key={c.id}
              role="listitem"
              className="group flex min-h-row-md items-center gap-1.5 px-panel py-0.5 hover:bg-surface-hover"
            >
              {confirming ? (
                <div className="flex min-w-0 flex-1 items-center justify-between gap-2 py-0.5">
                  <span className="min-w-0 truncate text-body text-status-critical">
                    {t('sidebar.comp.deleteConfirm', { name: typeLabel(c.type) })}
                  </span>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      data-testid={`confirm-remove-${c.id}`}
                      className="rounded-xs bg-status-critical px-2 py-0.5 text-micro font-medium text-white"
                      onClick={() => {
                        setConfirmRemoveId(null);
                        runLifecycle(c.id, { action: 'remove' });
                      }}
                    >
                      {t('sidebar.comp.confirmDelete')}
                    </button>
                    <button
                      type="button"
                      className="rounded-xs border border-edge-subtle px-2 py-0.5 text-micro text-ink-secondary"
                      onClick={() => setConfirmRemoveId(null)}
                    >
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <span className="min-w-0 flex-1 truncate text-body text-ink" title={c.id}>
                    {typeLabel(c.type)}
                    {binding && (
                      <span className="ml-1 text-micro text-ink-muted truncate">{binding}</span>
                    )}
                  </span>
                  {!c.enabled && <StatusBadge status="hidden" label={t('sidebar.comp.hidden')} />}
                  {c.collapsed && c.enabled && <StatusBadge status="unknown" label={t('sidebar.comp.collapsed')} />}

                  <div className="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    {actions.dock && (
                      <IconButton
                        size="sm"
                        label={
                          dockPlacements[c.id] === 'right'
                            ? `取消停靠 ${typeLabel(c.type)}`
                            : `停靠到右侧 ${typeLabel(c.type)}`
                        }
                        icon={PanelRight}
                        active={dockPlacements[c.id] !== undefined}
                        onClick={() => dockPanel(c.id, dockPlacements[c.id] === 'right' ? 'float' : 'right')}
                      />
                    )}
                    {actions.duplicate && (
                      <IconButton
                        size="sm"
                        label={`复制 ${typeLabel(c.type)}`}
                        icon={Copy}
                        onClick={() => runLifecycle(c.id, { action: 'duplicate' })}
                      />
                    )}
                    {actions.collapse && (
                      <IconButton
                        size="sm"
                        label={`折叠 ${typeLabel(c.type)}`}
                        icon={ChevronsDownUp}
                        onClick={() => run(c.id, toggleCollapsePatch(c))}
                      />
                    )}
                    {actions.expand && (
                      <IconButton
                        size="sm"
                        label={`展开 ${typeLabel(c.type)}`}
                        icon={ChevronsUpDown}
                        onClick={() => run(c.id, toggleCollapsePatch(c))}
                      />
                    )}
                    {actions.resetPosition && (
                      <IconButton
                        size="sm"
                        label={`重置位置 ${typeLabel(c.type)}`}
                        icon={Maximize}
                        onClick={() => run(c.id, resetPositionPatch(c))}
                      />
                    )}
                    {actions.bringToFront && (
                      <IconButton
                        size="sm"
                        label={`置顶 ${typeLabel(c.type)}`}
                        icon={ArrowUpToLine}
                        onClick={() => run(c.id, bringToFrontPatch(c, maxZ))}
                      />
                    )}
                    <IconButton
                      size="sm"
                      label={c.enabled ? `隐藏 ${typeLabel(c.type)}` : `显示 ${typeLabel(c.type)}`}
                      icon={c.enabled ? EyeOff : Eye}
                      active={c.enabled}
                      onClick={() => run(c.id, toggleVisibilityPatch(c))}
                    />
                    {actions.remove && (
                      <IconButton
                        size="sm"
                        label={`删除 ${typeLabel(c.type)}`}
                        icon={Trash2}
                        onClick={() => setConfirmRemoveId(c.id)}
                      />
                    )}
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ComponentsTab;
