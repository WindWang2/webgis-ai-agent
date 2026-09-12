'use client';

/**
 * LineageGraphView — 产物血缘 SVG DAG（ADR-0143 P3）。
 *
 * 为什么不复用 analysis-graph-panel（勘察报告 §1.5 偏差 2）：那是会话域
 * 执行 DAG 投影面板（ADR-0097），内部自取 session 分析图，模型为
 * capability DAG，与 artifact lineage 的 parents/consumers 边表不同源。
 * 本组件用 lineage-adapter 的分层布局做轻量 SVG 渲染：根产物居中，
 * 上游向左按 BFS depth 分层，下游向右。
 *
 * 可达性：图配 role=img + aria-label 概要；每个节点带 <title>；
 * 详情仍以按钮行为为准，不依赖悬停。
 */

import { useMemo } from 'react';

import type { LineageGraph } from '@/lib/api/project';
import { adaptLineage } from './lineage-adapter';

const COL_WIDTH = 120;
const ROW_HEIGHT = 34;
const NODE_W = 104;
const NODE_H = 24;
const PADDING = 12;

export interface LineageGraphViewProps {
  graph: LineageGraph;
  /** 节点点击（定位动作，P7 用）。 */
  onNodeClick?: (artifactId: string) => void;
}

export function LineageGraphView({ graph, onNodeClick }: LineageGraphViewProps) {
  const layout = useMemo(() => adaptLineage(graph), [graph]);

  const width =
    PADDING * 2 + (layout.maxColumn - layout.minColumn + 1) * COL_WIDTH;
  const height = PADDING * 2 + Math.max(layout.maxRows, 1) * ROW_HEIGHT;

  const x = (column: number) =>
    PADDING + (column - layout.minColumn) * COL_WIDTH + (COL_WIDTH - NODE_W) / 2;
  const y = (row: number) => PADDING + row * ROW_HEIGHT + (ROW_HEIGHT - NODE_H) / 2;

  const nodeById = useMemo(() => new Map(layout.nodes.map((n) => [n.id, n])), [layout]);
  const upstream = graph.parents?.length ?? 0;
  const downstream = graph.consumers?.length ?? 0;

  return (
    <div className="space-y-1">
      <p className="text-micro text-ink-muted">
        上游 {upstream} 条边 · 下游 {downstream} 条边（BFS 深度 ≤5，跨权限节点已由后端过滤）
      </p>
      <div className="overflow-auto rounded-sm border border-edge-subtle bg-surface-sunken">
        <svg
          role="img"
          aria-label={`产物 ${graph.artifact_id} 的血缘图：上游 ${upstream} 条边，下游 ${downstream} 条边，共 ${layout.nodes.length} 个节点`}
          width={width}
          height={height}
          className="text-ink-secondary"
        >
          {layout.edges.map((edge, i) => {
            const from = nodeById.get(edge.from);
            const to = nodeById.get(edge.to);
            if (!from || !to) return null;
            const x1 = x(from.column) + (edge.kind === 'parent' ? 0 : NODE_W);
            const y1 = y(from.row) + NODE_H / 2;
            const x2 = x(to.column) + (edge.kind === 'parent' ? NODE_W : 0);
            const y2 = y(to.row) + NODE_H / 2;
            const midX = (x1 + x2) / 2;
            return (
              <path
                key={`e-${i}`}
                d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                fill="none"
                stroke="currentColor"
                strokeWidth={1}
                opacity={0.5}
                className={edge.kind === 'parent' ? 'text-status-info' : 'text-status-success'}
              />
            );
          })}
          {layout.nodes.map((node) => {
            const isRoot = node.kind === 'root';
            return (
              <g
                key={node.id}
                transform={`translate(${x(node.column)}, ${y(node.row)})`}
                onClick={onNodeClick ? () => onNodeClick(node.id) : undefined}
                className={onNodeClick ? 'cursor-pointer' : undefined}
              >
                <title>
                  {`${node.kind === 'root' ? '当前产物' : node.kind === 'parent' ? '上游产物' : '下游产物'} ${node.id}${
                    node.producingTool ? `（工具 ${node.producingTool}）` : ''
                  }${node.sourceDatasetId ? ` · 源数据集 ${node.sourceDatasetId}` : ''}`}
                </title>
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={4}
                  className={
                    isRoot
                      ? 'fill-status-accent-soft stroke-status-accent'
                      : node.kind === 'parent'
                        ? 'fill-surface-raised stroke-status-info'
                        : 'fill-surface-raised stroke-status-success'
                  }
                  strokeWidth={isRoot ? 1.5 : 1}
                />
                <text
                  x={NODE_W / 2}
                  y={NODE_H / 2 + 3.5}
                  textAnchor="middle"
                  fontSize={9}
                  className={isRoot ? 'fill-ink-on-accent' : 'fill-ink'}
                >
                  {node.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
