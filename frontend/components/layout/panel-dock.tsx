'use client';

import React, { useCallback, useEffect, useMemo } from 'react';
import { PanelRightClose, PanelBottomClose } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useSyncExternalStore } from 'react';
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { renderComponent } from '@/components/map/map-components';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { RendererContext } from '@/components/map/map-components/types';

/**
 * Panel Dock Host（Workspace V2 / Goal C5）—— 轻量 dock 基座的渲染面。
 *
 * - 停靠的面板实例（chart/statistics）从地图 chrome 的 FloatingChrome
 *   定位体系移到右/下停靠区渲染；**内容渲染器与 chrome 同源**（同一
 *   registry + 同一 placement 语义 —— dock 只换宿主，不换组件实现）；
 * - dock 归属是工作区 UI 状态（dockSlice），与语义组件状态（MapSpec
 *   placement/enabled/collapsed）分离 —— 停靠不写 MapSpec，浮动不丢数据；
 * - 多面板停靠时以标签页切换（单面板直接展示）；
 * - 渲染上下文（zoom/centerLat/bearing）取自 HUD viewport —— 面板内容
 *   不依赖地图实例本身（图表/统计均为数据面板）。
 */

function useRendererContext(): RendererContext {
  const zoom = useHudStore((s) => Math.round(s.viewport?.zoom ?? 10));
  const centerLat = useHudStore((s) => s.viewport?.center?.[1] ?? 30);
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  // committed spec 是唯一地图真相 —— 图例/色条渲染器按 layerId 回读。
  const spec = useMemo<MapSpec | null>(
    () => getCommittedMapSpec(),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generation drives the re-read
    [specGeneration],
  );
  return useMemo(
    () => ({ spec, zoom, centerLat, bearing: 0 }),
    [spec, zoom, centerLat],
  );
}

function DockedPanelBody({ componentId }: { componentId: string }) {
  const ctx = useRendererContext();
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  const component = useMemo(() => {
    const spec = getCommittedMapSpec();
    return (spec?.layout?.components ?? []).find((c) => c.id === componentId) ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- specGeneration drives re-lookup
  }, [componentId, specGeneration]);
  if (!component || component.enabled === false) return null;
  const node = renderComponent(component, ctx);
  return <>{node}</>;
}

function DockChrome({
  region,
  title,
  onClose,
  tabs,
  activePanel,
  onSelect,
}: {
  region: 'right' | 'bottom';
  title: string;
  onClose: () => void;
  tabs: Array<{ id: string; label: string }>;
  activePanel: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      data-dock-region={region}
      className={
        region === 'right'
          ? 'absolute right-0 top-0 bottom-0 z-40 flex w-[340px] max-w-[85vw] flex-col border-l border-edge-subtle bg-surface-panel/95 backdrop-blur-sm shadow-panel'
          : 'absolute left-0 right-0 bottom-0 z-40 flex h-[300px] max-h-[60vh] flex-col border-t border-edge-subtle bg-surface-panel/95 backdrop-blur-sm shadow-panel'
      }
      role="region"
      aria-label={title}
    >
      <div className="flex shrink-0 items-center gap-1 border-b border-edge-subtle px-panel py-1">
        <span className="eyebrow">{title}</span>
        <span className="flex-1" />
        {tabs.length > 1 && (
          <div role="tablist" aria-label="停靠面板" className="flex items-center gap-0.5">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={tab.id === activePanel}
                onClick={() => onSelect(tab.id)}
                className={
                  tab.id === activePanel
                    ? 'rounded-xs border border-status-info-border bg-status-info-soft px-1.5 py-0.5 text-micro text-status-info'
                    : 'rounded-xs px-1.5 py-0.5 text-micro text-ink-muted transition-colors hover:text-ink'
                }
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          aria-label={region === 'right' ? '收起右侧停靠区' : '收起底部停靠区'}
          onClick={onClose}
          className="rounded-xs p-0.5 text-ink-muted transition-colors hover:text-ink"
        >
          {region === 'right' ? (
            <PanelRightClose aria-hidden className="h-icon-md w-icon-md" />
          ) : (
            <PanelBottomClose aria-hidden className="h-icon-md w-icon-md" />
          )}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-panel">
        {activePanel ? <DockedPanelBody key={activePanel} componentId={activePanel} /> : null}
      </div>
    </div>
  );
}

function panelLabel(type: string, id: string): string {
  if (type === 'chart_panel') return '图表';
  if (type === 'statistics_panel') return '统计';
  return id;
}

export function PanelDockHost() {
  // committed spec 变化（面板增删/重命名/禁用）时重算标签与实例。
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  const rightDock = useHudStore((s) => s.rightDock);
  const bottomDock = useHudStore((s) => s.bottomDock);
  const toggleRightDock = useHudStore((s) => s.toggleRightDock);
  const toggleBottomDock = useHudStore((s) => s.toggleBottomDock);
  const setActiveDockPanel = useHudStore((s) => s.setActiveDockPanel);

  // 面板标题来自 committed spec 的组件类型（dock 状态不复制语义标签）。
  // spec 变化（增删/重命名）驱动重建 id→type 表；dock 面板列表是调用参数。
  const tabsById = useMemo(() => {
    const spec = getCommittedMapSpec();
    return new Map((spec?.layout?.components ?? []).map((c) => [c.id, c] as const));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generation drives the re-read
  }, [specGeneration]);
  const pruneDockPanels = useHudStore((s) => s.pruneDockPanels);
  useEffect(() => {
    // spec 演进：离开 MapSpec 的组件实例，其 dock 归属失效（不留空壳/幽灵）。
    pruneDockPanels(new Set(tabsById.keys()));
  }, [tabsById, pruneDockPanels]);
  const tabsFor = useCallback(
    (ids: string[]) =>
      ids
        .map((id) => {
          const comp = tabsById.get(id);
          return comp ? { id, label: panelLabel(comp.type, id) } : null;
        })
        .filter((t): t is { id: string; label: string } => t !== null),
    [tabsById],
  );

  return (
    <>
      {(() => {
        // 只渲染 spec 里仍存在的面板（同会话 spec 演进可能移除实例）；
        // 全部失效时停靠区整体不渲染 —— 不出现空壳。
        const rightTabs = tabsFor(rightDock.panels)
        if (rightDock.open && rightTabs.length > 0) {
          const fallback = rightTabs[rightTabs.length - 1].id
          return (
            <DockChrome
              region="right"
              title="停靠面板"
              onClose={toggleRightDock}
              tabs={rightTabs}
              activePanel={rightTabs.some((t) => t.id === rightDock.activePanel)
                ? (rightDock.activePanel as string)
                : fallback}
              onSelect={(id) => setActiveDockPanel('right', id)}
            />
          )
        }
        const bottomTabs = tabsFor(bottomDock.panels)
        if (bottomDock.open && bottomTabs.length > 0) {
          const fallback = bottomTabs[bottomTabs.length - 1].id
          return (
            <DockChrome
              region="bottom"
              title="停靠面板"
              onClose={toggleBottomDock}
              tabs={bottomTabs}
              activePanel={bottomTabs.some((t) => t.id === bottomDock.activePanel)
                ? (bottomDock.activePanel as string)
                : fallback}
              onSelect={(id) => setActiveDockPanel('bottom', id)}
            />
          )
        }
        return null
      })()}
    </>
  );
}

export default PanelDockHost;
