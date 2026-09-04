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
  return (
    <div className="space-y-1 rounded-sm border border-edge-subtle bg-surface-sunken p-2 text-micro text-ink-secondary">
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-muted">查询指纹</span>
        <span className="truncate font-mono" title={evidence.query_fingerprint ?? ''}>
          {evidence.query_fingerprint ?? '—'}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-muted">传输行数 / 返回行数</span>
        <span className="font-mono">
          {evidence.rows_fetched ?? '—'} / {evidence.rows_returned ?? '—'}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-ink-muted">下推命中</span>
        <span className="truncate font-mono" title={pushedKeys.join(', ')}>
          {pushedKeys.length > 0 ? pushedKeys.join(', ') : '无'}
        </span>
      </div>
    </div>
  );
}

/** 错误展示：InlineNotice + error_type 等宽 chip。 */
function TypedErrorNotice({ error, label }: { error: TypedQueryError; label: string }) {
  return (
    <InlineNotice variant="error">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{label}：</span>
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
  if (!item) {
    return (
      <div className="flex-1 overflow-y-auto p-2">
        <EmptyState
          icon={FileSearch}
          title="尚未选择数据集"
          description="在「数据集」子页签构建查询后，计划与结果将在此展示"
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
      <section aria-label="查询计划" className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-body font-semibold text-ink">查询计划</h4>
          <span className="truncate font-mono text-micro text-ink-muted">{item.id}</span>
        </div>

        {explaining && <LoadingState label="正在生成查询计划..." />}

        {!explaining && explainError && <TypedErrorNotice error={explainError} label="解释计划失败" />}

        {!explaining && !explainError && explainLines.length === 0 && (
          <p className="text-meta text-ink-muted">
            尚未生成计划：在「数据集」子页签点击「解释计划」查看下推划分与估算。
          </p>
        )}

        {explainLines.length > 0 && (
          <>
            {/* 计划行 monospace 块（后端保证无 secret/连接 URI）。 */}
            <pre
              aria-label="查询计划详情"
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
                    估算 {plan.estimated_rows} 行
                  </span>
                )}
                {plan.pagination_strategy && (
                  <span className="rounded-pill border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
                    分页 {plan.pagination_strategy}
                  </span>
                )}
                {plan.result_mode && (
                  <span className="rounded-pill border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
                    模式 {plan.result_mode}
                  </span>
                )}
              </div>
            )}
            {explainResult?.dataset_fingerprint && (
              <p className="text-micro text-ink-muted">
                数据集指纹 <span className="font-mono">{explainResult.dataset_fingerprint}</span>
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
      <section aria-label="查询结果" className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-body font-semibold text-ink">查询结果</h4>
          {queryResult?.result_mode === 'sample' && <StatusBadge status="info" label="采样" />}
          {queryResult?.is_demo && <StatusBadge status="warning" label="演示数据" />}
          {queryResult?.truncated && <StatusBadge status="warning" label="已截断" />}
        </div>

        {querying && <LoadingState label="正在执行查询..." />}

        {!querying && queryError && <TypedErrorNotice error={queryError} label="查询失败" />}

        {!querying && !queryError && !hasResult && hasAnyContent && (
          <p className="text-meta text-ink-muted">尚未执行查询：在「数据集」子页签点击「执行查询」。</p>
        )}

        {!querying && !queryError && queryResult && (
          <>
            {/* 命中统计 */}
            <p className="text-micro text-ink-muted">
              返回 <span className="font-mono text-ink-secondary">{queryResult.returned_count ?? queryResult.features.length}</span> 行
              {queryResult.total_matching != null && (
                <>
                  {' '}· 命中约 <span className="font-mono text-ink-secondary">{queryResult.total_matching}</span> 行
                </>
              )}
              {queryResult.has_more && ' · 还有更多'}
            </p>

            {isStatistics ? (
              /* 统计模式：聚合行表格 + 证据（不渲染要素网格）。 */
              <div className="space-y-2">
                {aggRows.length === 0 ? (
                  <p className="text-meta text-ink-muted">聚合结果为空（无匹配分组）。</p>
                ) : (
                  <div className="max-h-64 overflow-auto rounded border border-edge-subtle bg-surface-sunken">
                    <table
                      role="table"
                      aria-label="聚合结果"
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
                描述符模式：仅返回数据集元数据（零数据传输），详情见上方契约摘要。
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
                  aria-label="服务端分页"
                >
                  {isCursorMode ? (
                    <button
                      type="button"
                      onClick={() => queryResult.next_cursor && onCursorNext(queryResult.next_cursor)}
                      disabled={!queryResult.next_cursor}
                      className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
                    >
                      <ChevronRight size={12} aria-hidden />
                      <span>下一页（游标）</span>
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
                        <span>上一页</span>
                      </button>
                      <span className="font-mono text-micro">第 {page + 1} 页</span>
                      <button
                        type="button"
                        onClick={() => onOffsetPage((page + 1) * pageSize)}
                        disabled={!queryResult.has_more && queryResult.features.length < pageSize}
                        className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
                      >
                        <span>下一页</span>
                        <ChevronRight size={12} aria-hidden />
                      </button>
                    </>
                  )}
                  {queryResult.is_demo && (
                    <span className="flex items-center gap-1 text-micro text-status-warning">
                      <FlaskConical size={11} aria-hidden />
                      远端不可达，返回演示数据
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
