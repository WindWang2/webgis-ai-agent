'use client';

/**
 * AgentRunPanel — V7 Agent 执行可观察面（停靠右侧区的静态面板）。
 *
 * 回答「Agent 正在如何处理 GIS 状态」，而不是只滚聊天文本：
 * - 当前阶段（aiStatus）+ 用户活动工具（toolSlice）；
 * - 意图与执行节点：analysis-graph 投影端点（有界摘要，非私有思维链 ——
 *   后端只披露结构化 goal/execution/product 与摘要理由）；
 * - 产物（results registry，step_id 键）：状态/警告/图层绑定，点击直达
 *   结果工作台检视（provenance 在 registry 内，不建第二 lineage）；
 * - 地图效应：opsLog 图层类操作（add/remove/toggle/style/reorder）近 8 条；
 * 刷新：手动（不轮询 —— 投影端点是会话级快照）。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw, Wrench, Package, Map as MapIcon, AlertTriangle, ListTree } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { getAnalysisGraph, type ExecutionNode } from '@/lib/api/analysis-graph';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { EmptyState } from '@/components/shared/empty-state';
import { StatusBadge } from '@/components/shared/status-badge';
import type { MapToolId } from '@/lib/store/slices/toolSlice';
import { useT } from '@/lib/i18n/useT';

const MAP_EFFECT_TYPES = new Set(['add', 'remove', 'toggle', 'style', 'reorder', 'sketch']);

/** 工具名展示词表：值 = agent namespace 下的消息 key（渲染处 t() 解析）。 */
const TOOL_LABEL_KEYS: Record<MapToolId, string> = {
  measure_distance: 'runPanel.tools.measureDistance',
  measure_area: 'runPanel.tools.measureArea',
  brush_select: 'runPanel.tools.brushSelect',
  draw_point: 'runPanel.tools.drawPoint',
  draw_line: 'runPanel.tools.drawLine',
  draw_polygon: 'runPanel.tools.drawPolygon',
  edit_vertices: 'runPanel.tools.editVertices',
  delete_feature: 'runPanel.tools.deleteFeature',
};

/** 阶段名展示词表：键 = aiStatus 逻辑值（保留），值 = 消息 key。 */
const PHASE_LABEL_KEYS: Record<string, string> = {
  idle: 'runPanel.phase.idle',
  thinking: 'runPanel.phase.thinking',
  acting: 'runPanel.phase.acting',
  done: 'runPanel.phase.done',
  error: 'runPanel.phase.error',
};

function ExecutionRow({ node }: { node: ExecutionNode }) {
  const t = useT('agent');
  const statusBadge = node.status === 'complete'
    ? 'ready'
    : node.status === 'running'
      ? 'loading'
      : node.status === 'failed'
        ? 'failed'
        : 'hidden';
  return (
    <li className="flex items-center gap-1.5 py-0.5" data-testid={`run-node-${node.id}`}>
      <StatusBadge status={statusBadge} />
      <span className="min-w-0 flex-1 truncate text-micro text-ink" title={node.purpose ?? node.capability}>
        {node.purpose || node.capability}
      </span>
      {node.algorithm && (
        <span className="shrink-0 font-mono text-micro text-ink-muted" title={t('runPanel.methodTitle', { algorithm: node.algorithm })}>
          {node.algorithm}
        </span>
      )}
    </li>
  );
}

export function AgentRunPanel() {
  const t = useT('agent');
  const aiStatus = useHudStore((s) => s.aiStatus);
  const activeTool = useHudStore((s) => s.activeMapTool);
  const results = useHudStore((s) => s.results);
  const selectResult = useHudStore((s) => s.selectResult);
  const opsLog = useHudStore((s) => s.opsLog);
  const setActiveLeftTab = useHudStore((s) => s.setActiveLeftTab);
  const setSelectedArtifactId = useHudStore((s) => s.setSelectedArtifactId);

  const [graph, setGraph] = useState<Awaited<ReturnType<typeof getAnalysisGraph>>>(null);
  const [loadingGraph, setLoadingGraph] = useState(false);
  // V7（review MINOR-9）：请求序号守卫 —— 快速连点刷新时旧响应不得覆盖新响应。
  const graphSeqRef = useRef(0);

  const refreshGraph = useCallback(async () => {
    const { sessionId, ownerToken } = getSessionIdentity();
    if (!sessionId) {
      setGraph(null);
      return;
    }
    const seq = ++graphSeqRef.current;
    setLoadingGraph(true);
    const g = await getAnalysisGraph(sessionId, ownerToken ?? null);
    if (seq !== graphSeqRef.current) return; // superseded
    setGraph(g);
    setLoadingGraph(false);
  }, []);

  useEffect(() => {
    void refreshGraph();
  }, [refreshGraph]);

  const executionNodes = useMemo(
    () => (graph?.nodes ?? []).filter((n): n is ExecutionNode => n.kind === 'analysis').slice(0, 6),
    [graph],
  );
  const mapEffects = useMemo(
    () => opsLog.filter((op) => MAP_EFFECT_TYPES.has(op.type)).slice(0, 8),
    [opsLog],
  );
  const warnedResults = useMemo(
    () => results.filter((r) => r.warnings?.length > 0).length,
    [results],
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto" data-testid="agent-run-panel">
      {/* 阶段 + 活动工具 */}
      <section aria-labelledby="run-phase-heading" className="space-y-1">
        <h3 id="run-phase-heading" className="eyebrow">{t('runPanel.currentExecution')}</h3>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-xs px-1.5 py-0.5 text-micro font-medium ${
              aiStatus === 'error' ? 'bg-status-critical-soft text-status-critical' : 'bg-status-accent-soft text-status-accent'
            }`}
            data-testid="run-phase"
          >
            {PHASE_LABEL_KEYS[aiStatus] ? t(PHASE_LABEL_KEYS[aiStatus]) : aiStatus}
          </span>
          {activeTool && (
            <span className="flex items-center gap-1 rounded-xs bg-surface-subtle px-1 py-0.5 text-micro text-ink-secondary">
              <Wrench aria-hidden size={11} />
              {TOOL_LABEL_KEYS[activeTool] ? t(TOOL_LABEL_KEYS[activeTool]) : activeTool}
            </span>
          )}
        </div>
      </section>

      {/* 意图与执行节点（analysis-graph 有界投影） */}
      <section aria-labelledby="run-graph-heading" className="space-y-1">
        <div className="flex items-center gap-1">
          <h3 id="run-graph-heading" className="eyebrow flex items-center gap-1">
            <ListTree aria-hidden size={11} />
            {t('runPanel.graphHeading')}
          </h3>
          <button
            type="button"
            aria-label={t('runPanel.refreshGraph')}
            className="ml-auto rounded-xs p-0.5 text-ink-muted hover:bg-surface-hover hover:text-ink"
            onClick={() => void refreshGraph()}
          >
            <RefreshCw aria-hidden size={11} className={loadingGraph ? 'animate-spin' : ''} />
          </button>
        </div>
        {graph?.goal ? (
          <p className="text-micro text-ink-secondary" title={t('runPanel.goalTitle')}>
            {t('runPanel.goalLabel', { goal: graph.goal.label ?? graph.goal.query ?? '—' })}
          </p>
        ) : (
          <p className="text-micro text-ink-muted">{loadingGraph ? t('runPanel.loadingGraph') : t('runPanel.noGraph')}</p>
        )}
        {executionNodes.length > 0 && (
          <ul className="m-0 list-none p-0">
            {executionNodes.map((node) => <ExecutionRow key={node.id} node={node} />)}
          </ul>
        )}
      </section>

      {/* 产物（results registry） */}
      <section aria-labelledby="run-artifacts-heading" className="space-y-1">
        <h3 id="run-artifacts-heading" className="eyebrow flex items-center gap-1">
          <Package aria-hidden size={11} />
          {t('runPanel.artifactsHeading', { count: results.length })}
          {warnedResults > 0 && (
            <span className="flex items-center gap-0.5 rounded-xs bg-status-warn-soft px-1 text-micro text-status-warn">
              <AlertTriangle aria-hidden size={10} />
              {t('runPanel.warnedCount', { count: warnedResults })}
            </span>
          )}
        </h3>
        {results.length === 0 ? (
          <p className="text-micro text-ink-muted">{t('runPanel.noArtifacts')}</p>
        ) : (
          <ul className="m-0 list-none space-y-0.5 p-0">
            {results.slice(0, 8).map((r) => (
              <li key={r.id} className="flex items-center gap-1.5">
                <StatusBadge status={r.running ? 'loading' : r.status === 'completed' ? 'ready' : r.status === 'failed' ? 'failed' : 'stale'} />
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left text-micro text-ink hover:text-status-accent"
                  title={t('runPanel.inspectArtifact', {
                    tool: r.toolLabel,
                    summary: r.summary ? `\n${r.summary}` : '',
                  })}
                  onClick={() => {
                    selectResult(r.id);
                    const firstRef = r.outputs?.[0]?.ref;
                    if (firstRef) setSelectedArtifactId(String(firstRef));
                    setActiveLeftTab('results');
                  }}
                >
                  {r.toolLabel}
                </button>
                {r.layerBindings?.length > 0 && (
                  <span
                    className="flex shrink-0 items-center gap-0.5 text-micro text-ink-muted"
                    title={t('runPanel.mountedLayers', { layers: r.layerBindings.map((b) => b.layerId).join(', ') })}
                  >
                    <MapIcon aria-hidden size={10} />
                    {r.layerBindings.length}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 地图效应 */}
      <section aria-labelledby="run-effects-heading" className="space-y-1">
        <h3 id="run-effects-heading" className="eyebrow">{t('runPanel.effectsHeading')}</h3>
        {mapEffects.length === 0 ? (
          <p className="text-micro text-ink-muted">{t('runPanel.noEffects')}</p>
        ) : (
          <ul className="m-0 list-none space-y-0.5 p-0">
            {mapEffects.map((op) => (
              <li key={op.id} className="flex items-center gap-1.5 text-micro text-ink-secondary">
                <span className="shrink-0 font-mono text-ink-disabled">{op.time}</span>
                <span className="min-w-0 flex-1 truncate">{op.label}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {aiStatus === 'idle' && results.length === 0 && !graph && (
        <EmptyState
          icon={ListTree}
          title={t('runPanel.emptyTitle')}
          description={t('runPanel.emptyDescription')}
        />
      )}
    </div>
  );
}

export default AgentRunPanel;
