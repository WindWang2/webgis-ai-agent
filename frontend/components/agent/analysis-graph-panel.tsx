'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Network, RefreshCw, AlertTriangle, ChevronRight } from 'lucide-react';
import type {
  AnalysisGraph,
  ExecutionNode,
  ProductFacetNode,
} from '@/lib/api/analysis-graph';
import { getAnalysisGraph } from '@/lib/api/analysis-graph';
import { useT } from '@/lib/i18n/useT';

/**
 * 显式分析图面板（ADR-0097）—— Agent Workspace 的核心检视面。
 *
 * 用户与 Agent 看同一份世界状态：目标（+方法论警告）、执行 DAG
 * （capability 依赖/状态/算法/工具）、产品 facets（完成度/重算维度）、
 * 下一动作。全部只读派生投影 —— 面板永不写状态。降级约定：无计划 →
 * 空态卡；端点失败 → 面板隐藏（不阻塞聊天面）。
 */

// 状态/模式词表：值是 chat ns 键（analysisGraph.*）；查不到键时回退显示原始值。
const EXEC_STATUS_KEYS: Record<string, string> = {
  pending: 'analysisGraph.execStatus.pending',
  ready: 'analysisGraph.execStatus.ready',
  running: 'analysisGraph.execStatus.running',
  complete: 'analysisGraph.execStatus.complete',
  skipped: 'analysisGraph.execStatus.skipped',
  unavailable: 'analysisGraph.execStatus.unavailable',
  failed: 'analysisGraph.execStatus.failed',
};

const EXEC_STATUS_CLASS: Record<string, string> = {
  complete: 'text-status-success',
  ready: 'text-status-info',
  running: 'text-status-info animate-pulse',
  failed: 'text-status-danger',
  unavailable: 'text-ink-disabled',
  skipped: 'text-ink-disabled',
  pending: 'text-ink-secondary',
};

const FACET_STATUS_KEYS: Record<string, string> = {
  complete: 'analysisGraph.facetStatus.complete',
  pending: 'analysisGraph.facetStatus.pending',
  failed: 'analysisGraph.facetStatus.failed',
  needs_repair: 'analysisGraph.facetStatus.needs_repair',
  off: 'analysisGraph.facetStatus.off',
};

const FACET_STATUS_CLASS: Record<string, string> = {
  complete: 'text-status-success',
  needs_repair: 'text-status-warning',
  failed: 'text-status-danger',
  pending: 'text-ink-secondary',
  off: 'text-ink-disabled',
};

const NEXT_ACTION_MODE_KEYS: Record<string, string> = {
  capability: 'analysisGraph.nextActionMode.capability',
  runtime_repair: 'analysisGraph.nextActionMode.runtime_repair',
  observation: 'analysisGraph.nextActionMode.observation',
  finalization: 'analysisGraph.nextActionMode.finalization',
};

function MethodologyWarnings({ graph }: { graph: AnalysisGraph }) {
  const t = useT('chat');
  const warnings = graph.goal?.methodology_warnings ?? [];
  if (warnings.length === 0) return null;
  return (
    <div
      className="rounded-md border border-status-warning/40 bg-status-warning/5 p-2"
      data-testid="analysis-graph-warnings"
      role="note"
    >
      <div className="mb-1 flex items-center gap-1 text-meta font-semibold text-status-warning">
        <AlertTriangle className="h-3 w-3" aria-hidden />
        {t('analysisGraph.warningsTitle', { count: warnings.length })}
      </div>
      <ul className="flex flex-col gap-1">
        {warnings.map((w, i) => (
          <li key={`${w.code}-${i}`} className="text-micro leading-snug text-ink">
            {w.code ? (
              <span className="mr-1 font-mono text-ink-secondary">{w.code}</span>
            ) : null}
            {w.disclosures.length > 0
              ? w.disclosures.join(' ')
              : t('analysisGraph.missingRoles', { roles: w.missing_roles.join(t('analysisGraph.rolesSeparator')) })}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ExecutionRow({ node }: { node: ExecutionNode }) {
  const t = useT('chat');
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(
    node.depends_on.length ||
      node.algorithm ||
      node.blocked_by.length ||
      node.fallback_to ||
      node.notes.length,
  );
  const execStatusKey = EXEC_STATUS_KEYS[node.status];
  const execStatusLabel = execStatusKey ? t(execStatusKey) : node.status;
  if (!hasDetail) {
    // review M-F8：无明细不渲染可聚焦的假展开按钮（控件必须真实可作用）。
    return (
      <li
        className="flex items-center gap-1.5 rounded-md border border-edge-subtle px-2 py-1"
        data-capability={node.capability}
        data-status={node.status}
      >
        <span className="w-3 shrink-0" />
        <span className="min-w-0 flex-1 truncate text-caption text-ink">
          {node.purpose || node.capability}
          {node.optional ? (
            <span className="ml-1 text-micro text-ink-disabled">{t('analysisGraph.optional')}</span>
          ) : null}
        </span>
        <span className={`shrink-0 text-micro ${EXEC_STATUS_CLASS[node.status] ?? ''}`}>
          {execStatusLabel}
        </span>
      </li>
    );
  }
  return (
    <li className="rounded-md border border-edge-subtle">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 px-2 py-1 text-left"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-capability={node.capability}
        data-status={node.status}
      >
        <ChevronRight
          className={`h-3 w-3 shrink-0 text-ink-disabled transition-transform ${open ? 'rotate-90' : ''}`}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate text-caption text-ink">
          {node.purpose || node.capability}
          {node.optional ? (
            <span className="ml-1 text-micro text-ink-disabled">{t('analysisGraph.optional')}</span>
          ) : null}
        </span>
        <span className={`shrink-0 text-micro ${EXEC_STATUS_CLASS[node.status] ?? ''}`}>
          {execStatusLabel}
        </span>
      </button>
      {open ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 border-t border-edge-subtle px-2 py-1 text-micro text-ink-secondary">
          <dt className="font-medium">{t('analysisGraph.detail.capability')}</dt>
          <dd className="font-mono">{node.capability}</dd>
          {node.algorithm ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.algorithm')}</dt>
              <dd className="font-mono">{node.algorithm}</dd>
            </>
          ) : null}
          {node.tool ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.tool')}</dt>
              <dd className="font-mono">{node.tool}</dd>
            </>
          ) : null}
          {node.depends_on.length > 0 ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.dependsOn')}</dt>
              <dd className="font-mono">{node.depends_on.join(' ← ')}</dd>
            </>
          ) : null}
          {node.blocked_by.length > 0 ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.blockedBy')}</dt>
              <dd className="font-mono text-status-warning">{node.blocked_by.join(', ')}</dd>
            </>
          ) : null}
          {node.fallback_to ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.fallback')}</dt>
              <dd className="font-mono">{node.fallback_to}</dd>
            </>
          ) : null}
          {node.bound_ref ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.artifact')}</dt>
              <dd className="truncate font-mono">{node.bound_ref}</dd>
            </>
          ) : null}
          {node.notes.length > 0 ? (
            <>
              <dt className="font-medium">{t('analysisGraph.detail.notes')}</dt>
              <dd>{node.notes.join('; ')}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
    </li>
  );
}

function FacetRow({ node }: { node: ProductFacetNode }) {
  const t = useT('chat');
  const facetStatusKey = FACET_STATUS_KEYS[node.status];
  const facetStatusLabel = facetStatusKey ? t(facetStatusKey) : node.status;
  return (
    <li
      className="flex items-center gap-1.5 px-2 py-1 text-caption"
      data-facet={node.facet_kind}
      data-status={node.status}
    >
      <span className="min-w-0 flex-1 truncate text-ink">
        {node.label || node.facet_kind}
        {node.required ? null : (
          <span className="ml-1 text-micro text-ink-disabled">{t('analysisGraph.optional')}</span>
        )}
      </span>
      <span className={`shrink-0 text-micro ${FACET_STATUS_CLASS[node.status] ?? ''}`}>
        {facetStatusLabel}
      </span>
    </li>
  );
}

interface Props {
  sessionId?: string | null;
  ownerToken?: string | null;
  /** 会话事件驱动的软刷新信号（每次 SSE 事件可递增） */
  refreshKey?: number;
}

export function AnalysisGraphPanel({ sessionId, ownerToken, refreshKey = 0 }: Props) {
  const t = useT('chat');
  const [graph, setGraph] = useState<AnalysisGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  // review M-F4：请求序号守卫 —— 手动刷新与 refreshKey 效果并发时，只有
  // 最新一次请求的结果可入态（旧响应到达晚不回写）；卸载时整体失效。
  const requestSeq = useRef(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    const seq = ++requestSeq.current;
    setLoading(true);
    const g = await getAnalysisGraph(sessionId, ownerToken);
    if (!alive.current || seq !== requestSeq.current) return; // 过期响应丢弃
    setLoading(false);
    if (g === null) {
      // review M-F5：刷新失败保留 last-good（面板不闪没、刷新按钮仍在）；
      // 仅首载失败才是「隐藏面板」的诚实语义。
      setRefreshFailed(true);
      return;
    }
    setRefreshFailed(false);
    setGraph(g);
  }, [sessionId, ownerToken]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  if (!sessionId) return null;
  if (!graph) {
    // 首载中给骨架头（review m15：不留死表达式/无反馈空白）；首载失败 →
    // 面板隐藏（与 SessionPlanPanel 同降级约定）。
    if (loading) {
      return (
        <section
          className="rounded-lg border border-edge-subtle bg-surface-panel p-2"
          data-testid="analysis-graph-panel"
          aria-label={t('analysisGraph.title')}
        >
          <header className="flex items-center gap-1.5">
            <Network className="h-3.5 w-3.5 text-ink-secondary" aria-hidden />
            <h3 className="flex-1 text-meta font-semibold text-ink">{t('analysisGraph.title')}</h3>
            <RefreshCw className="h-3 w-3 animate-spin text-ink-secondary" aria-hidden />
          </header>
        </section>
      );
    }
    return null;
  }
  const execNodes = graph.nodes.filter(
    (n): n is ExecutionNode => n.kind === 'requirement' || n.kind === 'analysis',
  );
  const facets = graph.nodes.filter(
    (n): n is ProductFacetNode => n.kind === 'product',
  );
  const nextMode = graph.next_action?.mode ?? '';
  const nextModeKey = NEXT_ACTION_MODE_KEYS[nextMode];
  const nextModeLabel = nextModeKey ? t(nextModeKey) : nextMode;

  return (
    <section
      className="rounded-lg border border-edge-subtle bg-surface-panel p-2"
      data-testid="analysis-graph-panel"
      aria-label={t('analysisGraph.title')}
    >
      <header className="mb-1.5 flex items-center gap-1.5">
        <Network className="h-3.5 w-3.5 text-ink-secondary" aria-hidden />
        <h3 className="flex-1 text-meta font-semibold text-ink">{t('analysisGraph.title')}</h3>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded p-1 text-ink-secondary hover:bg-surface-sunken hover:text-ink"
          aria-label={t('analysisGraph.refreshAria')}
        >
          <RefreshCw
            className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`}
            aria-hidden
          />
        </button>
      </header>

      {graph.goal ? (
        <div className="mb-1.5">
          <div className="truncate text-caption font-medium text-ink" title={graph.goal.label}>
            {graph.goal.label || t('analysisGraph.goalUntitled')}
          </div>
          <div className="text-micro text-ink-secondary">
            {graph.goal.recipe_id || t('analysisGraph.goalNoRecipe')}
            {graph.goal.superseded ? t('analysisGraph.supersededSuffix') : ''}
          </div>
        </div>
      ) : (
        <div className="px-1 py-3 text-center text-caption text-ink-disabled" data-state="empty">
          {t('analysisGraph.emptyPlan')}
        </div>
      )}

      {refreshFailed ? (
        <div
          className="mb-1.5 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-micro text-ink-secondary"
          role="status"
          data-testid="analysis-graph-refresh-error"
        >
          {t('analysisGraph.refreshFailedNotice')}
        </div>
      ) : null}
      <MethodologyWarnings graph={graph} />

      {graph.next_action ? (
        <div
          className="mt-1.5 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-micro text-ink"
          data-testid="analysis-graph-next-action"
        >
          <span className="font-semibold">
            {t('analysisGraph.nextActionTitle', { mode: nextModeLabel })}
          </span>{' '}
          {graph.next_action.reason}
        </div>
      ) : null}

      {execNodes.length > 0 ? (
        <details className="mt-1.5" open>
          <summary className="cursor-pointer text-meta font-semibold text-ink-secondary">
            {t('analysisGraph.execStepsTitle', { count: execNodes.length })}
          </summary>
          <ul className="mt-1 flex flex-col gap-1">
            {execNodes.map((n) => (
              <ExecutionRow key={n.id} node={n} />
            ))}
          </ul>
        </details>
      ) : null}

      {facets.length > 0 ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-meta font-semibold text-ink-secondary">
            {t('analysisGraph.productFacetsTitle', { count: facets.length })}
          </summary>
          <ul className="mt-1 flex flex-col divide-y divide-edge-subtle rounded-md border border-edge-subtle">
            {facets.map((n) => (
              <FacetRow key={n.id} node={n} />
            ))}
          </ul>
        </details>
      ) : null}

      {graph.notes.length > 0 ? (
        <div className="mt-1.5 text-micro text-ink-disabled">
          {graph.notes.join(t('analysisGraph.notesSeparator'))}
        </div>
      ) : null}
    </section>
  );
}
