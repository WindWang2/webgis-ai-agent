'use client';

import { useCallback, useRef, useState } from 'react';
import {
  Boxes,
  GitMerge,
  Satellite,
  Send,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { CatalogEntry, CatalogOwnerType } from '@/lib/api/lakehouse';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { CatalogToolbar } from './catalog-toolbar';
import { CatalogItemCard } from './catalog-item-card';
import { ObjectDetailPanel } from './object-detail-panel';
import { DatasetsPanel } from './datasets-panel';
import { QueryPane } from './query-pane';
import { useLakehouseCatalog, EMPTY_CATALOG_FILTERS, type CatalogFilters } from './use-lakehouse-catalog';

/**
 * 数据湖 tab（ADR-0141）—— Lakehouse V8 能力面的唯一前端入口。
 *
 * 六个子页签：目录（catalog 检索+manifest 检视）/ 数据集（V8 版本层）/
 * 查询（window·labeled 窗口读+上图）/ STAC（1.0.0 投影浏览）/
 * 发布（publish·revoke·快照 diff）/ 运维（verify·scrub·gc·血缘，只读）。
 *
 * 跨子页签动线：目录卡片「查询」→ 查询页带 cube ref；「血缘」→ 运维页
 * 定位血缘。会话凭据（sessionId/ownerToken）由 ContextPanel 注入（#463 同款
 * 语义），全部请求 fail-closed 404 时以 InlineNotice 呈现。
 */

export const LAKEHOUSE_SUBTABS = [
  { key: 'catalog', label: '目录' },
  { key: 'datasets', label: '数据集' },
  { key: 'query', label: '查询' },
  { key: 'stac', label: 'STAC' },
  { key: 'publish', label: '发布' },
  { key: 'ops', label: '运维' },
] as const;

export type LakehouseSubTab = (typeof LAKEHOUSE_SUBTABS)[number]['key'];

export interface LakehouseTabProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

export function LakehouseTab({ sessionId, ownerToken }: LakehouseTabProps) {
  const [activeSubTab, setActiveSubTab] = useState<LakehouseSubTab>('catalog');
  const [ownerType, setOwnerType] = useState<CatalogOwnerType>('session');
  const [projectId, setProjectId] = useState('');
  const [filters, setFilters] = useState<CatalogFilters>(EMPTY_CATALOG_FILTERS);
  const [offset, setOffset] = useState(0);
  const [detailEntry, setDetailEntry] = useState<CatalogEntry | null>(null);
  /** 查询构建器预选 cube ref（目录卡片「查询」动线）。 */
  const [queryTarget, setQueryTarget] = useState<string | null>(null);
  /** 运维页定位的血缘对象。 */
  const [lineageTarget, setLineageTarget] = useState<string | null>(null);

  // WAI-APG tablist：roving tabindex + 方向键/Home/End（activation-on-focus），
  // 与 nav-rail / data-sources 子页签同一配方。
  const subTabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const onSubTabKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const idx = LAKEHOUSE_SUBTABS.findIndex((t) => t.key === activeSubTab);
      let next: LakehouseSubTab | null = null;
      if (e.key === 'ArrowRight') next = LAKEHOUSE_SUBTABS[(idx + 1) % LAKEHOUSE_SUBTABS.length].key;
      else if (e.key === 'ArrowLeft')
        next = LAKEHOUSE_SUBTABS[(idx - 1 + LAKEHOUSE_SUBTABS.length) % LAKEHOUSE_SUBTABS.length].key;
      else if (e.key === 'Home') next = LAKEHOUSE_SUBTABS[0].key;
      else if (e.key === 'End') next = LAKEHOUSE_SUBTABS[LAKEHOUSE_SUBTABS.length - 1].key;
      if (!next) return;
      e.preventDefault();
      setActiveSubTab(next);
      subTabRefs.current.get(next)?.focus();
    },
    [activeSubTab],
  );

  const ownerId = ownerType === 'session' ? (sessionId ?? '') : projectId;
  const catalog = useLakehouseCatalog({
    ownerType,
    ownerId,
    sessionId: sessionId ?? '',
    ownerToken,
    filters,
    offset,
  });

  const switchTo = useCallback((tab: LakehouseSubTab) => {
    setActiveSubTab(tab);
  }, []);

  const openQuery = useCallback(
    (item: CatalogEntry) => {
      // catalog 条目只有 object_id（64hex manifest 身份）；查询需要 cube ref
      // —— 查询构建器收到 object_id 后经 manifest payload 解析 ref（解析不出
      // 则诚实报错，绝不臆测 ref:cube/<id> 的映射）。
      setQueryTarget(item.object_id);
      switchTo('query');
    },
    [switchTo],
  );

  const openLineage = useCallback(
    (objectId: string) => {
      setLineageTarget(objectId);
      switchTo('ops');
    },
    [switchTo],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="lakehouse-tab">
      {/* 子页签（V4 tablist 配方） */}
      <div
        role="tablist"
        aria-label="数据量子页签"
        className="flex shrink-0 gap-1 border-b border-edge-subtle px-panel pt-1"
        onKeyDown={onSubTabKeyDown}
      >
        {LAKEHOUSE_SUBTABS.map(({ key, label }) => {
          const active = activeSubTab === key;
          return (
            <button
              key={key}
              ref={(el) => {
                if (el) subTabRefs.current.set(key, el);
                else subTabRefs.current.delete(key);
              }}
              role="tab"
              aria-selected={active}
              aria-controls={`lakehouse-panel-${key}`}
              id={`lakehouse-tab-${key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => setActiveSubTab(key)}
              className={`-mb-px rounded-t-sm border border-b-0 px-2 py-1 text-caption font-medium transition-colors ${
                active
                  ? 'border-edge-subtle bg-surface-panel text-ink'
                  : 'border-transparent text-ink-secondary hover:bg-surface-hover hover:text-ink'
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* 面板（切换即卸载，与 ContextPanel 其他 tab 一致） */}
      {activeSubTab === 'catalog' && (
        <div
          role="tabpanel"
          id="lakehouse-panel-catalog"
          aria-labelledby="lakehouse-tab-catalog"
          className="flex min-h-0 flex-1 flex-col"
        >
          {!sessionId && ownerType === 'session' ? (
            <EmptyState
              icon={Boxes}
              title="暂无活跃会话"
              description="Lakehouse 目录按会话域隔离 —— 打开或创建会话后在此浏览。"
            />
          ) : (
            <>
              <CatalogToolbar
                ownerType={ownerType}
                onOwnerTypeChange={(t) => {
                  setOwnerType(t);
                  setOffset(0);
                  setDetailEntry(null);
                }}
                projectId={projectId}
                onProjectIdChange={setProjectId}
                filters={filters}
                onFiltersChange={(f) => {
                  setFilters(f);
                  setOffset(0);
                }}
                onRefresh={catalog.refresh}
                loading={catalog.loading}
              />
              {detailEntry ? (
                <ObjectDetailPanel
                  entry={detailEntry}
                  sessionId={sessionId ?? ''}
                  ownerToken={ownerToken}
                  onBack={() => setDetailEntry(null)}
                  onOpenLineage={openLineage}
                />
              ) : (
                <div className="min-h-0 flex-1 overflow-y-auto px-panel py-2" data-testid="lakehouse-catalog-list">
                  {catalog.error && <InlineNotice variant="error">{catalog.error}</InlineNotice>}
                  {catalog.loading && <LoadingState label="正在检索目录…" />}
                  {!catalog.loading && !catalog.error && catalog.items.length === 0 && (
                    <EmptyState
                      icon={Boxes}
                      title="目录为空"
                      description="该域内还没有可浏览的 DataObject；cube 构建后会出现在这里。"
                    />
                  )}
                  <ul className="space-y-1.5">
                    {catalog.items.map((item) => (
                      <li key={item.object_id}>
                        <CatalogItemCard
                          item={item}
                          onShowDetail={setDetailEntry}
                          onQuery={openQuery}
                          onLineage={(it) => openLineage(it.object_id)}
                        />
                      </li>
                    ))}
                  </ul>
                  {/* offset 分页（next_offset null = 无更多页；total 是诚实字符串） */}
                  {(offset > 0 || catalog.nextOffset !== null) && !catalog.loading && (
                    <div className="flex items-center justify-between py-2 text-caption text-ink-secondary">
                      <button
                        type="button"
                        disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - 50))}
                        className="rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover disabled:opacity-40"
                      >
                        上一页
                      </button>
                      <span title={catalog.totalBounded ? undefined : '超扫描下界（诚实形态）'}>
                        共 {catalog.total} 条
                      </span>
                      <button
                        type="button"
                        disabled={catalog.nextOffset === null}
                        onClick={() => setOffset(catalog.nextOffset ?? offset)}
                        className="rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover disabled:opacity-40"
                      >
                        下一页
                      </button>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {activeSubTab === 'datasets' && (
        <div
          role="tabpanel"
          id="lakehouse-panel-datasets"
          aria-labelledby="lakehouse-tab-datasets"
          className="flex min-h-0 flex-1 flex-col"
        >
          <DatasetsPanel ownerType={ownerType} sessionId={sessionId ?? ''} ownerToken={ownerToken} />
        </div>
      )}

      {activeSubTab === 'query' && (
        <div
          role="tabpanel"
          id="lakehouse-panel-query"
          aria-labelledby="lakehouse-tab-query"
          className="flex min-h-0 flex-1 flex-col"
        >
          <QueryPane
            sessionId={sessionId ?? ''}
            ownerToken={ownerToken}
            objectIdHint={queryTarget}
            onHintConsumed={() => setQueryTarget(null)}
          />
        </div>
      )}

      {activeSubTab === 'stac' && (
        <div
          role="tabpanel"
          id="lakehouse-panel-stac"
          aria-labelledby="lakehouse-tab-stac"
          className="flex min-h-0 flex-1 flex-col"
        >
          <PlaceholderPane icon={Satellite} label="STAC 目录" />
        </div>
      )}

      {activeSubTab === 'publish' && (
        <div
          role="tabpanel"
          id="lakehouse-panel-publish"
          aria-labelledby="lakehouse-tab-publish"
          className="flex min-h-0 flex-1 flex-col"
        >
          <PlaceholderPane icon={Send} label="发布与快照对比" />
        </div>
      )}

      {activeSubTab === 'ops' && (
        <div
          role="tabpanel"
          id="lakehouse-panel-ops"
          aria-labelledby="lakehouse-tab-ops"
          className="flex min-h-0 flex-1 flex-col"
        >
          <PlaceholderPane
            icon={GitMerge}
            label="运维与血缘"
            detail={lineageTarget ? `待检视对象：${lineageTarget.slice(0, 16)}…（P8 接入血缘与 gc/scrub 只读面）` : undefined}
          />
        </div>
      )}
    </div>
  );
}

function PlaceholderPane({
  icon: Icon,
  label,
  detail,
}: {
  icon: LucideIcon;
  label: string;
  detail?: string;
}) {
  return (
    <EmptyState
      icon={Icon}
      title={`${label}（建设子页签）`}
      description={detail ?? '查询 / STAC / 发布 / 运维面在本线后续阶段接入。'}
    />
  );
}

export default LakehouseTab;
