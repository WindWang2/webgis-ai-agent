'use client';

import { ChevronLeft, ChevronRight, FlaskConical, FileSearch } from 'lucide-react';
import type {
  CatalogItem,
  ExplainResult,
  QueryEvidenceInfo,
  QueryPlanInfo,
  QueryResult,
} from '@/lib/api/data-fabric';
import { TabularDataGrid } from '@/components/explorer/tabular-data-grid';
import { StatusBadge } from '@/components/shared/status-badge';
import { InlineNotice } from '@/components/shared/inline-notice';
import { EmptyState } from '@/components/shared/empty-state';
import { LoadingState } from '@/components/shared/loading-state';
import { useT } from '@/lib/i18n/useT';

/** 类型化错误（后端 DataFabricError.to_dict 的 error_type/error）。 */
export interface TypedQueryError {
  message: string;
  errorType?: string;
}

export interface ExplainResultsPanelProps {
  item: CatalogItem | null;
  explainResult: ExplainResult | null;
  explaining: boolean;
  explainError: TypedQueryError | null;
  queryResult: QueryResult | null;
  querying: boolean;
  queryError: TypedQueryError | null;
  /** 最近一次执行 spec 的 limit（offset 翻页步长）。 */
  pageSize: number;
  /** 当前 offset 页（0 起）。 */
  page: number;
  /** cursor 翻页：携带 next_cursor 重新执行。 */
  onCursorNext: (cursor: string) => void;
  /** offset 翻页：携带绝对 offset 重新执行。 */
  onOffsetPage: (offset: number) => void;
}

/** unknown → 平面 record（metadata 窄化用）。 */
function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

/** QueryResult.metadata.query_plan → QueryPlanInfo（缺失返回 null）。 */
export function readQueryPlan(result: QueryResult | null): QueryPlanInfo | null {
  const meta = asRecord(result?.metadata);
  return (asRecord(meta?.query_plan) as QueryPlanInfo | null) ?? null;
}

/** QueryResult.metadata.query_evidence → QueryEvidenceInfo（缺失返回 null）。 */
export function readQueryEvidence(result: QueryResult | null): QueryEvidenceInfo | null {
  const meta = asRecord(result?.metadata);
  return (asRecord(meta?.query_evidence) as QueryEvidenceInfo | null) ?? null;
}

/** pushdown ✓/✗ 徽标（WAI：aria-label 携带完整语义）。 */
function PushdownBadge({ label, pushed }: { label: string; pushed: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-pill border px-1.5 py-0.5 font-mono text-micro ${
        pushed
          ? 'border-status-success-border bg-status-success-soft text-status-success'
          : 'border-status-neutral-border bg-status-neutral-soft text-status-neutral'
      }`}
      aria-label={`${label}下推${pushed ? '已启用' : '未启用'}`}
    >
      <span aria-hidden>{pushed ? '✓' : '✗'}</span>
      {label}
    </span>
  );
}

/** 证据摘要行：query fingerprint / rows fetched vs returned / pushdown 命中。 */
function EvidenceSummary({ evidence }: { evidence: QueryEvidenceInfo }) {
  const pushedKeys = Object.entries(evidence.pushdowns ?? {})
    .filter(([, v]) => v === true)
    .map(([k]) => k);
  const t = useT();
  return (
    <div className="space-y-1 rounded-sm border border-edge-subtle bg-surface-sunken p-2 text-micro text-ink-secondary">
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-muted">{t('sidebar.explain.queryFingerprint')}</span>
        <span className="truncate font-mono" title={evidence.query_fingerprint ?? ''}>
          {evidence.query_fingerprint ?? '—'}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-muted">{t('sidebar.explain.rowsTransferred')}</span>
        <span className="font-mono">
          {evidence.rows_fetched ?? '—'} / {evidence.rows_returned ?? '—'}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-muted">{t('sidebar.explain.pushdownHits')}</span>
        <span className="truncate font-mono" title={pushedKeys.join(', ')}>
          {pushedKeys.length > 0 ? pushedKeys.join(', ') : '无'}
        </span>
      </div>
    </div>
  );
}

/** 错误展示：InlineNotice + error_type 等宽 chip。 */
function TypedErrorNotice({ error, label }: { error: TypedQueryError; label: string }) {
  const t = useT();
  return (
    <InlineNotice variant="error">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{label}{t('sidebar.explain.colon')}</span>
        <span>{error.message}</span>
        {error.errorType && (
          <code className="rounded-sm bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-status-critical">
            {error.errorType}
          </code>
        )}
      </div>
    </InlineNotice>
  );
}

/**
 * 查询计划与结果面板（ADR-0094 §13）。
 *
 * - explain：后端计划行（monospace 块）+ pushdown/估算/分页/结果模式徽标 + warnings；
 * - features/sample：首页表格 + 服务端分页（cursor 下一页 / offset 步进器）；
 * - statistics：聚合行表格 + 执行证据（指纹 / 行数 / 下推摘要）——不渲染要素网格；
 * - 错误：describeApiError 文案 + error_type chip。
 */
export function ExplainResultsPanel({
  item,
  explainResult,
  explaining,
  explainError,
  queryResult,
  querying,
  queryError,
  pageSize,
  page,
  onCursorNext,
  onOffsetPage,
}: ExplainResultsPanelProps) {
  const t = useT();
  if (!item) {
    return (
      <div className="flex-1 overflow-y-auto p-2">
        <EmptyState
          icon={FileSearch}
          title={t('sidebar.inspector.emptyTitle')}
          description={t('sidebar.explain.emptyDesc')}
        />
      </div>
    );
  }

  const plan = readQueryPlan(queryResult) ?? explainResult?.plan ?? null;
  const evidence = readQueryEvidence(queryResult);
  const explainLines = explainResult?.explain ?? [];
  const resultMode = queryResult?.result_mode ?? 'features';
  const isStatistics = resultMode === 'statistics';
  const resultData = queryResult?.data;
  const aggRows: Array<Record<string, unknown>> = Array.isArray(resultData)
    ? (resultData as Array<Record<string, unknown>>)
    : [];
  const aggColumns: string[] = (() => {
    const schemaCols = asRecord(queryResult?.schema_info)?.columns;
    if (Array.isArray(schemaCols) && schemaCols.length > 0) return schemaCols.map(String);
    return aggRows.length > 0 ? Object.keys(aggRows[0]) : [];
  })();
  const strategy = plan?.pagination_strategy ?? (queryResult?.next_cursor ? 'cursor' : 'offset');
  const isCursorMode = strategy === 'cursor' && !!queryResult?.next_cursor;
  const hasResult = !!queryResult;
  const hasAnyContent = hasResult || explainLines.length > 0 || !!explainError || !!queryError;

  return (
    <div className="flex-1 space-y-3 overflow-y-auto p-2">
      {/* ── explain 计划 ─────────────────────────────────────────────── */}
      <section aria-label={t('sidebar.explain.planTitle')} className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-body font-semibold text-ink">{t('sidebar.explain.planTitle')}</h4>
          <span className="truncate font-mono text-micro text-ink-muted">{item.id}</span>
        </div>

        {explaining && <LoadingState label={t('sidebar.explain.planning')} />}

        {!explaining && explainError && <TypedErrorNotice error={explainError} label={t('sidebar.explain.planFailed')} />}

        {!explaining && !explainError && explainLines.length === 0 && (
          <p className="text-meta text-ink-muted">
            {t('sidebar.explain.noPlan')}
          </p>
        )}

        {explainLines.length > 0 && (
          <>
            {/* 计划行 monospace 块（后端保证无 secret/连接 URI）。 */}
            <pre
              aria-label={t('sidebar.explain.planDetailAria')}
              className="max-h-52 overflow-auto rounded-sm border border-edge-subtle bg-surface-sunken p-2 font-mono text-caption leading-relaxed text-ink-secondary"
            >
              {explainLines.join('\n')}
            </pre>
            {plan && (
              <div className="flex flex-wrap items-center gap-1.5">
                <PushdownBadge label="bbox" pushed={plan.pushed_spatial === true} />
                <PushdownBadge label="filter" pushed={(plan.pushed_filters?.length ?? 0) > 0} />
                <PushdownBadge label="projection" pushed={plan.pushed_projection === true} />
                <PushdownBadge label="aggregation" pushed={plan.pushed_aggregation === true} />
                {plan.estimated_rows != null && (
                  <span className="rounded-pill border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
                    {t('sidebar.explain.estimateRows', { rows: plan.estimated_rows })}
                  </span>
                )}
                {plan.pagination_strategy && (
                  <span className="rounded-pill border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
                    {t('sidebar.explain.pagination')} {plan.pagination_strategy}
                  </span>
                )}
                {plan.result_mode && (
                  <span className="rounded-pill border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
                    {t('sidebar.explain.mode')} {plan.result_mode}
                  </span>
                )}
              </div>
            )}
            {explainResult?.dataset_fingerprint && (
              <p className="text-micro text-ink-muted">
                {t('sidebar.inspector.fingerprint')} <span className="font-mono">{explainResult.dataset_fingerprint}</span>
              </p>
            )}
            {(plan?.warnings?.length ?? 0) > 0 && (
              <InlineNotice variant="warning">
                <ul className="list-disc space-y-0.5 pl-4">
                  {plan!.warnings!.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </InlineNotice>
            )}
          </>
        )}
      </section>

      {/* ── 查询结果 ─────────────────────────────────────────────────── */}
      <section aria-label={t('sidebar.explain.resultTitle')} className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-body font-semibold text-ink">{t('sidebar.explain.resultTitle')}</h4>
          {queryResult?.result_mode === 'sample' && <StatusBadge status="info" label={t('sidebar.explain.sampled')} />}
          {queryResult?.is_demo && <StatusBadge status="warning" label={t('sidebar.explain.demoData')} />}
          {queryResult?.truncated && <StatusBadge status="warning" label={t('sidebar.explain.truncated')} />}
        </div>

        {querying && <LoadingState label={t('sidebar.explain.running')} />}

        {!querying && queryError && <TypedErrorNotice error={queryError} label={t('sidebar.explain.queryFailed')} />}

        {!querying && !queryError && !hasResult && hasAnyContent && (
          <p className="text-meta text-ink-muted">{t('sidebar.explain.noResult')}</p>
        )}

        {!querying && !queryError && queryResult && (
          <>
            {/* 命中统计 */}
            <p className="text-micro text-ink-muted">
              {t('sidebar.explain.returnedRows')} <span className="font-mono text-ink-secondary">{queryResult.returned_count ?? queryResult.features.length}</span> {t('sidebar.explain.rowsUnit')}
              {queryResult.total_matching != null && (
                <>
                  {' '}{t('sidebar.explain.matchedApprox')} <span className="font-mono text-ink-secondary">{queryResult.total_matching}</span> {t('sidebar.explain.rowsUnit')}
                </>
              )}
              {queryResult.has_more && ' · 还有更多'}
            </p>

            {isStatistics ? (
              /* 统计模式：聚合行表格 + 证据（不渲染要素网格）。 */
              <div className="space-y-2">
                {aggRows.length === 0 ? (
                  <p className="text-meta text-ink-muted">{t('sidebar.explain.emptyAgg')}</p>
                ) : (
                  <div className="max-h-64 overflow-auto rounded border border-edge-subtle bg-surface-sunken">
                    <table
                      role="table"
                      aria-label={t('sidebar.explain.aggResultAria')}
                      className="w-full border-collapse text-left text-meta"
                    >
                      <thead className="sticky top-0 bg-surface-raised">
                        <tr className="border-b border-edge-subtle text-caption font-semibold text-ink">
                          {aggColumns.map((c) => (
                            <th key={c} scope="col" className="whitespace-nowrap px-2.5 py-1.5">
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-edge-subtle bg-surface-panel">
                        {aggRows.map((row, i) => (
                          <tr key={i}>
                            {aggColumns.map((c) => (
                              <td key={c} className="px-2.5 py-1 font-mono text-caption text-ink">
                                {row[c] === null || row[c] === undefined ? '—' : String(row[c])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {evidence && <EvidenceSummary evidence={evidence} />}
              </div>
            ) : queryResult.result_mode === 'descriptor' ? (
              <p className="text-meta text-ink-muted">
                {t('sidebar.explain.descriptorMode')}
              </p>
            ) : (
              /* features / sample 模式：首页要素表格 + 服务端分页。 */
              <>
                <TabularDataGrid
                  data={queryResult}
                  totalCount={
                    queryResult.total_matching ?? queryResult.total_count ?? queryResult.features.length
                  }
                  defaultPageSize={Math.min(Math.max(pageSize, 10), 100)}
                  emptyTitle={querying ? undefined : '查询无结果'}
                  emptyDescription="当前查询条件下未返回要素，请调整 where / bbox 后重试"
                />
                {/* 服务端分页（非表格内置客户端分页）。 */}
                <div
                  className="flex items-center justify-between gap-2 border-t border-edge-subtle pt-2 text-caption text-ink-secondary"
                  role="group"
                  aria-label={t('sidebar.explain.serverPaginationAria')}
                >
                  {isCursorMode ? (
                    <button
                      type="button"
                      onClick={() => queryResult.next_cursor && onCursorNext(queryResult.next_cursor)}
                      disabled={!queryResult.next_cursor}
                      className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
                    >
                      <ChevronRight size={12} aria-hidden />
                      <span>{t('sidebar.explain.nextCursor')}</span>
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => onOffsetPage(Math.max(0, (page - 1) * pageSize))}
                        disabled={page <= 0}
                        className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
                      >
                        <ChevronLeft size={12} aria-hidden />
                        <span>{t('sidebar.explain.prevPage')}</span>
                      </button>
                      <span className="font-mono text-micro">{t('sidebar.explain.pageOf', { page: page + 1 })}</span>
                      <button
                        type="button"
                        onClick={() => onOffsetPage((page + 1) * pageSize)}
                        disabled={!queryResult.has_more && queryResult.features.length < pageSize}
                        className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
                      >
                        <span>{t('sidebar.explain.nextPage')}</span>
                        <ChevronRight size={12} aria-hidden />
                      </button>
                    </>
                  )}
                  {queryResult.is_demo && (
                    <span className="flex items-center gap-1 text-micro text-status-warning">
                      <FlaskConical size={11} aria-hidden />
                      {t('sidebar.explain.fallbackDemo')}
                    </span>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}
