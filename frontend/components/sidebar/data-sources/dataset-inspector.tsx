'use client';

import { useId, useState } from 'react';
import { Download, Play, Sigma, Table2 } from 'lucide-react';
import type { CatalogItem, DatasetDescriptor, QuerySpec } from '@/lib/api/data-fabric';
import { useHudStore } from '@/lib/store/useHudStore';
import { StatusBadge } from '@/components/shared/status-badge';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { EmptyState } from '@/components/shared/empty-state';
import { STitle } from '@/components/shared/section-title';

/** 大数据集物化阈值（ADR-0094：超过该规模建议瓦片预览而非 inline 物化）。 */
export const MATERIALIZE_LARGE_DATASET_THRESHOLD = 5000;

/** 聚合函数词表（后端 AggSpec.func 契约）。 */
const AGG_FUNCS = ['count', 'sum', 'avg', 'min', 'max', 'stddev', 'distinct_count'] as const;

/** 无需字段的聚合（后端契约：count 可省略 field）。 */
const FIELDLESS_AGGS = new Set<string>(['count']);

export interface DatasetInspectorProps {
  /** 当前检视的目录项（空间目录中选择）。 */
  item: CatalogItem | null;
  descriptor: DatasetDescriptor | null;
  loadingDescriptor: boolean;
  /** dataset fingerprint 摘要（来自 explain 结果；explain 前不展示）。 */
  fingerprint?: string | null;
  querying: boolean;
  explaining: boolean;
  materializing: boolean;
  /** 组装好 QuerySpec 后交由父级执行（父级负责切到「查询计划」子页签）。 */
  onRunQuery: (spec: QuerySpec) => void;
  onExplain: (spec: QuerySpec) => void;
  onMaterialize: (item: CatalogItem) => void;
}

/**
 * 数据集检视器 + 安全查询构建器（ADR-0094 §13 数据工作台）。
 *
 * 内联面板（非 modal-only）：选中目录项后常驻侧栏，展示 descriptor 契约
 * （schema / CRS / 范围 / 要素数 / fingerprint / 可用性），以及可视化
 * QuerySpec 构建（字段投影 / where / bbox / limit / 排序 / 聚合）。
 * 聚合一旦设置，result_mode 自动切换为 statistics（零 geometry 传输）。
 */
export function DatasetInspector({
  item,
  descriptor,
  loadingDescriptor,
  fingerprint,
  querying,
  explaining,
  materializing,
  onRunQuery,
  onExplain,
  onMaterialize,
}: DatasetInspectorProps) {
  // ── 查询构建器状态 ────────────────────────────────────────────────────
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [whereText, setWhereText] = useState('');
  const [bboxEnabled, setBboxEnabled] = useState(false);
  const [useViewBbox, setUseViewBbox] = useState(true);
  const [bboxWest, setBboxWest] = useState('');
  const [bboxSouth, setBboxSouth] = useState('');
  const [bboxEast, setBboxEast] = useState('');
  const [bboxNorth, setBboxNorth] = useState('');
  const [limitText, setLimitText] = useState('100');
  const [orderField, setOrderField] = useState('');
  const [orderDirection, setOrderDirection] = useState<'asc' | 'desc'>('asc');
  const [aggFunc, setAggFunc] = useState('');
  const [aggField, setAggField] = useState('');
  const [groupByField, setGroupByField] = useState('');

  // 单字段选择器：仅数据集子页签挂载期间订阅视口（避免整 tab 随地图重渲染）。
  // bounds 为 [west, south, east, north]（map-panel move 结算写入，hud-types 契约）。
  const viewBounds = useHudStore((s) => s.viewport?.bounds);

  const fields = descriptor?.fields ?? [];
  const fieldNames = fields.map((f) => f.name);
  const whereId = useId();
  const limitId = useId();

  if (!item) {
    return (
      <div className="flex-1 overflow-y-auto p-2">
        <EmptyState
          icon={Table2}
          title="尚未选择数据集"
          description="在「空间目录」中点击条目的「数据集」按钮，检视契约并构建查询"
        />
      </div>
    );
  }

  const isUnavailable = (item.availability ?? 'available') === 'unavailable';
  const featureCount = descriptor?.feature_count ?? null;
  const largeDataset = featureCount !== null && featureCount > MATERIALIZE_LARGE_DATASET_THRESHOLD;

  /** 组装 QuerySpec（聚合存在时 result_mode 自动切 statistics）。 */
  const buildSpec = (): QuerySpec => {
    const spec: QuerySpec = {};
    if (selectedFields.length > 0) spec.fields = [...selectedFields];
    const where = whereText.trim();
    if (where) spec.where = where;
    if (bboxEnabled) {
      let bbox: number[] | null = null;
      if (useViewBbox) {
        // 视口 bounds 为 [west, south, east, north]（hud-types 契约）。
        const b = viewBounds;
        if (b && b.length === 4 && b.every((v) => typeof v === 'number' && Number.isFinite(v))) {
          bbox = [b[0], b[1], b[2], b[3]];
        }
      } else {
        const w = Number.parseFloat(bboxWest);
        const s = Number.parseFloat(bboxSouth);
        const e = Number.parseFloat(bboxEast);
        const n = Number.parseFloat(bboxNorth);
        if ([w, s, e, n].every((v) => Number.isFinite(v))) bbox = [w, s, e, n];
      }
      if (bbox) spec.bbox = bbox;
    }
    const limit = Number.parseInt(limitText, 10);
    if (Number.isFinite(limit) && limit > 0) spec.limit = limit;
    if (orderField) spec.order_by = [{ field: orderField, direction: orderDirection }];
    if (aggFunc) {
      // count 可无字段；其余聚合必须选字段（后端 AggSpec 契约）。
      const agg: { func: string; field?: string } = { func: aggFunc };
      if (aggField && !FIELDLESS_AGGS.has(aggFunc)) agg.field = aggField;
      spec.aggregate = [agg];
      if (groupByField) spec.group_by = [groupByField];
      // 聚合语义 → statistics（零 geometry 传输，ADR-0094 §4）。
      spec.result_mode = 'statistics';
    }
    return spec;
  };

  const aggNeedsField = aggFunc !== '' && !FIELDLESS_AGGS.has(aggFunc);

  const inputClass =
    'w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption text-ink placeholder:text-ink-disabled focus:border-status-accent-border focus:outline-none';
  const labelClass = 'text-caption text-ink-muted';
  const selectClass = `${inputClass} font-mono`;

  return (
    <div className="flex-1 space-y-3 overflow-y-auto p-2">
      {/* ── 契约摘要 ──────────────────────────────────────────────────── */}
      <section aria-label="数据集契约摘要">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h4 className="truncate text-body font-semibold text-ink">{item.title || item.name}</h4>
            <p className="mt-0.5 truncate font-mono text-micro text-ink-muted">{item.id}</p>
          </div>
          {isUnavailable && <StatusBadge status="stale" label="已下线" />}
        </div>

        {loadingDescriptor ? (
          <LoadingState label="正在加载数据集契约..." />
        ) : descriptor ? (
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-meta text-ink-secondary">
            <div className="col-span-2 flex justify-between gap-2">
              <dt className="shrink-0 text-ink-muted">要素总数</dt>
              <dd className="font-mono">{featureCount ?? '未知'}</dd>
            </div>
            <div className="col-span-2 flex justify-between gap-2">
              <dt className="shrink-0 text-ink-muted">SRS 坐标系</dt>
              <dd className="truncate font-mono">{descriptor.srs || item.crs || '未知'}</dd>
            </div>
            <div className="col-span-2 flex justify-between gap-2">
              <dt className="shrink-0 text-ink-muted">几何类型</dt>
              <dd className="font-mono">{descriptor.geometry_type || item.geometry_type || '未知'}</dd>
            </div>
            <div className="col-span-2 flex justify-between gap-2">
              <dt className="shrink-0 text-ink-muted">空间范围</dt>
              <dd className="truncate font-mono text-micro" title={JSON.stringify(descriptor.bbox ?? item.bbox)}>
                {descriptor.bbox?.length === 4
                  ? descriptor.bbox.map((v) => (typeof v === 'number' ? v.toFixed(3) : v)).join(', ')
                  : '未知'}
              </dd>
            </div>
            {fingerprint && (
              <div className="col-span-2 flex justify-between gap-2">
                <dt className="shrink-0 text-ink-muted">数据集指纹</dt>
                <dd className="truncate font-mono text-micro" title={fingerprint}>
                  {fingerprint.slice(0, 16)}…
                </dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="mt-2 text-meta text-ink-muted">契约信息不可用（获取 Descriptor 失败）</p>
        )}

        {fields.length > 0 && (
          <div className="mt-2">
            <p className={labelClass}>
              字段 Schema（{fields.length}）
            </p>
            <div className="mt-1 max-h-32 space-y-0.5 overflow-y-auto rounded-sm border border-edge-subtle bg-surface-sunken p-2 font-mono text-caption">
              {fields.map((f) => (
                <div key={f.name} className="flex justify-between gap-2">
                  <span className="truncate text-ink-secondary">{f.name}</span>
                  <span className="shrink-0 text-agent-accent">{f.type}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 大数据集物化守卫（任务 3）：提示瓦片预览，但不阻断既有物化行为。 */}
        {largeDataset && (
          <InlineNotice variant="warning" className="mt-2">
            该数据集规模较大（{featureCount} 个要素，阈值 {MATERIALIZE_LARGE_DATASET_THRESHOLD}）。
            建议优先使用瓦片预览或聚合统计；继续物化可能影响地图性能。
          </InlineNotice>
        )}
        {isUnavailable && (
          <InlineNotice variant="error" className="mt-2">
            该数据集已从数据源下线（元数据保留）：查询与物化可能失败，请先执行「同步」刷新目录。
          </InlineNotice>
        )}
      </section>

      {/* ── 查询构建器 ────────────────────────────────────────────────── */}
      <section aria-label="查询构建器" className="space-y-2 rounded-md border border-edge-subtle bg-surface-overlay p-2">
        <STitle title="查询构建器" sub="where 表达式遵循受限语法（单谓词 AND 连接）" />

        {/* 字段投影多选 */}
        <fieldset>
          <legend className={labelClass}>字段投影（不选 = 全部字段）</legend>
          {fields.length === 0 ? (
            <p className="mt-1 text-caption text-ink-disabled">契约字段不可用</p>
          ) : (
            <div
              role="group"
              aria-label="选择投影字段"
              className="mt-1 max-h-28 space-y-0.5 overflow-y-auto rounded-sm border border-edge-subtle bg-surface-sunken p-2"
            >
              {fields.map((f) => (
                <label key={f.name} className="flex cursor-pointer items-center gap-1.5 text-caption text-ink-secondary">
                  <input
                    type="checkbox"
                    aria-label={`选择字段 ${f.name}`}
                    checked={selectedFields.includes(f.name)}
                    onChange={(e) => {
                      setSelectedFields((prev) =>
                        e.target.checked ? [...prev, f.name] : prev.filter((n) => n !== f.name)
                      );
                    }}
                    className="rounded-sm border-edge-strong"
                    style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                  />
                  <span className="truncate font-mono">{f.name}</span>
                  <span className="ml-auto shrink-0 font-mono text-micro text-ink-disabled">{f.type}</span>
                </label>
              ))}
            </div>
          )}
          {selectedFields.length > 0 && (
            <button
              type="button"
              onClick={() => setSelectedFields([])}
              className="mt-1 text-micro text-ink-muted underline-offset-2 hover:text-ink hover:underline"
            >
              清空已选（{selectedFields.length}）
            </button>
          )}
        </fieldset>

        {/* where 表达式 */}
        <div className="flex flex-col gap-1">
          <label htmlFor={whereId} className={labelClass}>
            过滤表达式（where）
          </label>
          <input
            id={whereId}
            type="text"
            value={whereText}
            onChange={(e) => setWhereText(e.target.value)}
            placeholder="type = 'school' AND students > 500"
            className={`${inputClass} font-mono`}
          />
        </div>

        {/* bbox 空间过滤 */}
        <fieldset>
          <legend className={labelClass}>空间过滤（bbox）</legend>
          <label className="mt-1 flex cursor-pointer items-center gap-1.5 text-caption text-ink-secondary">
            <input
              type="checkbox"
              checked={bboxEnabled}
              onChange={(e) => setBboxEnabled(e.target.checked)}
              aria-label="启用 bbox 空间过滤"
              className="rounded-sm border-edge-strong"
              style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
            />
            <span>启用空间范围过滤</span>
          </label>
          {bboxEnabled && (
            <div className="mt-1.5 space-y-1.5 pl-4">
              <div className="flex gap-3 text-caption text-ink-secondary">
                <label className="flex cursor-pointer items-center gap-1">
                  <input
                    type="radio"
                    name="bbox-mode"
                    checked={useViewBbox}
                    onChange={() => setUseViewBbox(true)}
                    style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                  />
                  <span>使用当前视图范围</span>
                </label>
                <label className="flex cursor-pointer items-center gap-1">
                  <input
                    type="radio"
                    name="bbox-mode"
                    checked={!useViewBbox}
                    onChange={() => setUseViewBbox(false)}
                    style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                  />
                  <span>手动输入</span>
                </label>
              </div>
              {useViewBbox ? (
                <p className="text-micro text-ink-disabled">
                  {viewBounds && viewBounds.length === 4
                    ? `当前视图：${viewBounds.map((v) => v.toFixed(3)).join(', ')}`
                    : '暂无视图范围（地图尚未移动）；查询将不携带 bbox'}
                </p>
              ) : (
                <div className="grid grid-cols-4 gap-1.5">
                  {(
                    [
                      ['西', bboxWest, setBboxWest],
                      ['南', bboxSouth, setBboxSouth],
                      ['东', bboxEast, setBboxEast],
                      ['北', bboxNorth, setBboxNorth],
                    ] as Array<[string, string, (v: string) => void]>
                  ).map(([label, value, setter]) => (
                    <div key={label} className="flex flex-col gap-0.5">
                      <label className="text-micro text-ink-muted">{label}</label>
                      <input
                        type="number"
                        step="any"
                        value={value}
                        onChange={(e) => setter(e.target.value)}
                        aria-label={`bbox ${label}边界`}
                        className={`${inputClass} font-mono`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </fieldset>

        {/* limit + 排序 */}
        <div className="flex gap-2">
          <div className="w-24">
            <label htmlFor={limitId} className={labelClass}>
              行数上限
            </label>
            <input
              id={limitId}
              type="number"
              min={1}
              value={limitText}
              onChange={(e) => setLimitText(e.target.value)}
              className={`${inputClass} font-mono`}
            />
          </div>
          <div className="flex-1">
            <label htmlFor="qb-order-field" className={labelClass}>
              排序字段
            </label>
            <select
              id="qb-order-field"
              value={orderField}
              onChange={(e) => setOrderField(e.target.value)}
              className={selectClass}
            >
              <option value="">不排序</option>
              {fieldNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div className="w-20">
            <label htmlFor="qb-order-dir" className={labelClass}>
              方向
            </label>
            <select
              id="qb-order-dir"
              value={orderDirection}
              onChange={(e) => setOrderDirection(e.target.value === 'desc' ? 'desc' : 'asc')}
              disabled={!orderField}
              className={selectClass}
              aria-label="排序方向"
            >
              <option value="asc">升序</option>
              <option value="desc">降序</option>
            </select>
          </div>
        </div>

        {/* 聚合 */}
        <fieldset>
          <legend className={labelClass}>聚合（设置后自动切换为统计模式）</legend>
          <div className="mt-1 grid grid-cols-3 gap-1.5">
            <select
              value={aggFunc}
              onChange={(e) => {
                setAggFunc(e.target.value);
                setAggField('');
              }}
              aria-label="聚合函数"
              className={selectClass}
            >
              <option value="">无</option>
              {AGG_FUNCS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
            <select
              value={aggField}
              onChange={(e) => setAggField(e.target.value)}
              disabled={!aggNeedsField}
              aria-label="聚合字段"
              className={selectClass}
            >
              <option value="">{FIELDLESS_AGGS.has(aggFunc) ? '（无需字段）' : '选择字段'}</option>
              {fieldNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <select
              value={groupByField}
              onChange={(e) => setGroupByField(e.target.value)}
              disabled={!aggFunc}
              aria-label="分组字段（group by）"
              className={selectClass}
            >
              <option value="">不分组</option>
              {fieldNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          {aggFunc && (
            <p className="mt-1 text-micro text-ink-muted">
              <Sigma size={10} className="mr-0.5 inline" aria-hidden />
              result_mode 将切换为 statistics（仅返回聚合结果，不传输 geometry）
            </p>
          )}
        </fieldset>

        {/* 操作按钮 */}
        <div className="flex items-center gap-2 border-t border-edge-subtle pt-2">
          <button
            type="button"
            onClick={() => onRunQuery(buildSpec())}
            disabled={querying || explaining}
            className="flex items-center gap-1 rounded-sm bg-status-accent px-2.5 py-1 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-50"
          >
            <Play size={12} aria-hidden />
            <span>{querying ? '查询中...' : '执行查询'}</span>
          </button>
          <button
            type="button"
            onClick={() => onExplain(buildSpec())}
            disabled={querying || explaining}
            className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2.5 py-1 text-caption font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
          >
            <Sigma size={12} aria-hidden />
            <span>{explaining ? '分析中...' : '解释计划'}</span>
          </button>
          <button
            type="button"
            onClick={() => onMaterialize(item)}
            disabled={materializing}
            className="ml-auto flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-caption text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
          >
            <Download size={12} aria-hidden />
            <span>{materializing ? '实例化中...' : '物化到地图'}</span>
          </button>
        </div>
      </section>
    </div>
  );
}
