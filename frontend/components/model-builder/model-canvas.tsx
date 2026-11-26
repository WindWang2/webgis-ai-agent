'use client';

/**
 * ModelCanvas — 模型构建器自绘 SVG 画布（复刻 lineage-graph 分层布局与
 * 连边风格：bezier 边 + currentColor 描边 + 节点框）。
 *
 * 交互契约：
 * - 点击选中（外圈虚线框高亮，联动右属性面板）；节点 role=button +
 *   aria-label + tabIndex，Tab/方向键移动焦点，Delete 删除（连边级联）；
 * - 拖动只改 UI 坐标：拖拽期间坐标留在组件内（dragPos state），pointerup
 *   才经 onMoveNode 落父层 store —— 不触发任何 validate/compile 请求；
 * - 端口拖拽连线：拖拽中按 canConnect 纯函数给非法目标端口着红；松手
 *   非法 → onConnectRejected（父层弹 InlineNotice，不落 store）；
 *   合法 → onRequestConnect（父层写 store 并标 dirty）。
 * - 运行 overlay：nodeStates 把实例节点态映射为描边色/错误徽标
 *   （run-overlay.ts 纯函数；FAILED/BLOCKED 附 error_code 提示）。
 *
 * 渲染纪律：CanvasNode memo 化（编辑器上限 32 节点，无虚拟化必要）。
 */

import { memo, useCallback, useMemo, useRef, useState } from 'react';
import { Workflow } from 'lucide-react';
import clsx from 'clsx';
import { useT } from '@/lib/i18n/useT';
import { EmptyState } from '@/components/shared/empty-state';
import {
  canConnect,
  layeredLayout,
  nodeInputPorts,
  nodeOutputPorts,
  type CapabilityIOMap,
  type ModelDocument,
  type ModelLayoutPos,
  type ModelNode,
  type PortInfo,
} from '@/lib/workflow/model-graph';
import { authorNodeId, isFailingNodeState, NODE_STATE_STROKE } from './run-overlay';

const NODE_W = 148;
const NODE_H = 40;
const PORT_R = 5;
const CANVAS_MARGIN = 48;

/** kind → 节点框描边/底色（data_input/output 强调，其余中性底 + 状态色描边）。 */
const KIND_STYLE: Record<string, { stroke: string; fill: string; bar: string }> = {
  data_input: { stroke: 'stroke-status-accent', fill: 'fill-status-accent-soft', bar: 'fill-status-accent' },
  transform: { stroke: 'stroke-status-info', fill: 'fill-surface-raised', bar: 'fill-status-info' },
  analysis: { stroke: 'stroke-status-success', fill: 'fill-surface-raised', bar: 'fill-status-success' },
  cartography: { stroke: 'stroke-status-warning', fill: 'fill-surface-raised', bar: 'fill-status-warning' },
  output: { stroke: 'stroke-status-accent', fill: 'fill-status-accent-soft', bar: 'fill-status-accent' },
};

/** kind → i18n 键（全字面量 —— 键引用扫描器只认字面量/模板串）。 */
const KIND_LABEL_KEY: Record<string, string> = {
  data_input: 'node.kindDataInput',
  transform: 'node.kindTransform',
  analysis: 'node.kindAnalysis',
  cartography: 'node.kindCartography',
  output: 'node.kindOutput',
};

export interface ConnectRequest {
  fromNode: string;
  fromPort: string;
  toNode: string;
  toPort: string;
}

export interface ModelCanvasProps {
  document: ModelDocument;
  selectedNodeId: string | null;
  catalog?: CapabilityIOMap | null;
  /** 运行 overlay：作者 node_id → 实例节点态。 */
  nodeStates?: Record<string, string>;
  /** 运行 overlay：作者 node_id → error_code（FAILED/BLOCKED 展示）。 */
  nodeErrors?: Record<string, string>;
  /** 绑定回执：作者 node_id → {ok, code}。 */
  bindings?: Record<string, { ok: boolean; code: string; bound_ref?: string }>;
  onSelectNode: (nodeId: string | null) => void;
  /** 拖动结束（pointerup）提交 UI 坐标。 */
  onMoveNode: (nodeId: string, pos: ModelLayoutPos) => void;
  onDeleteNode: (nodeId: string) => void;
  /** 合法连线（父层写 store 并标 dirty）。 */
  onRequestConnect: (req: ConnectRequest) => void;
  /** 非法连线松手（父层弹 InlineNotice，reason 为 i18n key 后缀）。 */
  onConnectRejected: (reason: string) => void;
}

interface CanvasNodeProps {
  node: ModelNode;
  pos: ModelLayoutPos;
  selected: boolean;
  kindLabel: string;
  stateStroke?: string;
  failing: boolean;
  errorText: string;
  bindingText: string;
  inputPorts: PortInfo[];
  outputPorts: PortInfo[];
  connecting: boolean;
  /** 连线拖拽中：本节点输入端口是否合法目标（null = 非拖拽态）。 */
  portValidity: boolean | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, pos: ModelLayoutPos) => void;
  onPortDown: (nodeId: string, port: string) => void;
  onPortUp: (nodeId: string, port: string) => void;
}

/** 单节点渲染（memo 化 —— 拖拽只重渲染被拖节点与边层）。 */
const CanvasNode = memo(function CanvasNode({
  node,
  pos,
  selected,
  kindLabel,
  stateStroke,
  failing,
  errorText,
  bindingText,
  inputPorts,
  outputPorts,
  connecting,
  portValidity,
  onSelect,
  onDelete,
  onMove,
  onPortDown,
  onPortUp,
}: CanvasNodeProps) {
  const t = useT('modelBuilder');
  const [dragPos, setDragPos] = useState<ModelLayoutPos | null>(null);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; origin: ModelLayoutPos; moved: boolean } | null>(null);

  const style = KIND_STYLE[node.kind] ?? KIND_STYLE.transform;
  const title = node.title || node.node_id;
  const shown = dragPos ?? pos;

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if ((e.target as Element).closest('[data-port]')) return; // 端口自有拖拽语义
      e.stopPropagation();
      dragRef.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        origin: pos,
        moved: false,
      };
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    },
    [pos],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      const dx = e.clientX - d.startX;
      const dy = e.clientY - d.startY;
      if (!d.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      d.moved = true;
      // 拖拽期坐标只在组件内演进（UI 坐标），pointerup 才提交。
      setDragPos({
        x: Math.max(0, d.origin.x + dx),
        y: Math.max(0, d.origin.y + dy),
      });
    },
    [],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      dragRef.current = null;
      (e.currentTarget as Element).releasePointerCapture?.(e.pointerId);
      if (d.moved) {
        // 只提交坐标（不触发 validate/compile）。
        onMove(node.node_id, dragPos ?? d.origin);
        setDragPos(null);
      } else {
        onSelect(node.node_id);
      }
    },
    [dragPos, node.node_id, onMove, onSelect],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onSelect(node.node_id);
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        onDelete(node.node_id);
      }
    },
    [node.node_id, onDelete, onSelect],
  );

  const ariaLabel = `${t('aria.canvasNode', { title, kind: kindLabel })}${
    errorText ? ` ${errorText}` : ''
  }${bindingText ? ` ${bindingText}` : ''}`;

  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      data-testid={`canvas-node-${node.node_id}`}
      transform={`translate(${shown.x}, ${shown.y})`}
      className="cursor-pointer outline-none focus-visible:[&>rect]:stroke-status-accent"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onKeyDown={onKeyDown}
    >
      <title>{ariaLabel}</title>
      <rect
        width={NODE_W}
        height={NODE_H}
        rx={4}
        className={clsx(
          stateStroke ?? style.stroke,
          style.fill,
        )}
        strokeWidth={1.5}
      />
      {/* kind 色条（图标色的框内投影） */}
      <rect width={4} height={NODE_H} className={style.bar} rx={2} />
      <text x={10} y={NODE_H / 2 + 3.5} fontSize={11} className="fill-ink">
        {title.length > 18 ? `${title.slice(0, 17)}…` : title}
      </text>
      {/* 输入端口（左缘；data_input 无输入端口） */}
      {inputPorts.map((p, i) => {
        const invalid = connecting && portValidity === false;
        const valid = connecting && portValidity === true;
        return (
          <circle
            key={`in-${p.name}`}
            data-port="in"
            data-node-id={node.node_id}
            data-port-name={p.name}
            cx={0}
            cy={NODE_H / 2 + (i - (inputPorts.length - 1) / 2) * 14}
            r={PORT_R}
            role="button"
            aria-label={t('aria.inputPort', { port: p.name })}
            className={clsx(
              'cursor-crosshair',
              invalid
                ? 'fill-status-critical stroke-status-critical'
                : valid
                  ? 'fill-status-success stroke-status-success'
                  : 'fill-surface-panel stroke-ink-muted',
            )}
            strokeWidth={1.5}
            onPointerDown={(e) => e.stopPropagation()}
            onPointerUp={(e) => {
              e.stopPropagation();
              onPortUp(node.node_id, p.name);
            }}
          />
        );
      })}
      {/* 输出端口（右缘；output 汇点无输出端口） */}
      {outputPorts.map((p, i) => (
        <circle
          key={`out-${p.name}`}
          data-port="out"
          data-node-id={node.node_id}
          data-port-name={p.name}
          cx={NODE_W}
          cy={NODE_H / 2 + (i - (outputPorts.length - 1) / 2) * 14}
          r={PORT_R}
          role="button"
          aria-label={t('aria.outputPort', { port: p.name })}
          className="cursor-crosshair fill-surface-panel stroke-ink-secondary"
          strokeWidth={1.5}
          onPointerDown={(e) => {
            e.stopPropagation();
            onPortDown(node.node_id, p.name);
          }}
        />
      ))}
      {/* 选中外圈（虚线）—— 与运行态描边色解耦 */}
      {selected && (
        <rect
          x={-4}
          y={-4}
          width={NODE_W + 8}
          height={NODE_H + 8}
          rx={6}
          fill="none"
          className="stroke-status-accent"
          strokeWidth={1}
          strokeDasharray="4 3"
        />
      )}
      {/* 运行态失败徽标（FAILED/BLOCKED） */}
      {failing && (
        <circle
          cx={NODE_W - 8}
          cy={8}
          r={4}
          className="fill-status-critical"
        >
          <title>{errorText}</title>
        </circle>
      )}
    </g>
  );
});

export function ModelCanvas({
  document: doc,
  selectedNodeId,
  catalog,
  nodeStates,
  nodeErrors,
  bindings,
  onSelectNode,
  onMoveNode,
  onDeleteNode,
  onRequestConnect,
  onConnectRejected,
}: ModelCanvasProps) {
  const t = useT('modelBuilder');
  const [connecting, setConnecting] = useState<{ node: string; port: string } | null>(null);
  const ghostEnd = useRef<ModelLayoutPos | null>(null);
  const [, setGhostTick] = useState(0);

  // 展示坐标：doc.layout + 无坐标节点补分层布局值（不回写文档）。
  const positions = useMemo(() => {
    const layout = layeredLayout(doc).positions;
    return new Map(doc.nodes.map((n) => [n.node_id, layout[n.node_id] ?? { x: CANVAS_MARGIN, y: CANVAS_MARGIN }]));
  }, [doc]);

  const portsOf = useMemo(() => {
    // 变量名避开 `map` —— test/maplibre-mock-surface.test.ts 以源码扫描
    // `map.<method>` 调用面，局部 Map 命名冲突会被当成 MapLibre API 使用。
    const byNode = new Map<string, { ins: PortInfo[]; outs: PortInfo[] }>();
    for (const n of doc.nodes) {
      byNode.set(n.node_id, { ins: nodeInputPorts(n, catalog), outs: nodeOutputPorts(n, catalog) });
    }
    return byNode;
  }, [doc.nodes, catalog]);

  // 连线拖拽中：每个候选输入端口的合法性（纯函数预检，不请求后端）。
  const portValidity = useMemo(() => {
    if (!connecting) return null;
    const verdicts = new Map<string, boolean>();
    for (const n of doc.nodes) {
      for (const p of nodeInputPorts(n, catalog)) {
        verdicts.set(
          `${n.node_id}:${p.name}`,
          canConnect(doc, connecting.node, connecting.port, n.node_id, p.name, catalog).ok,
        );
      }
    }
    return verdicts;
  }, [connecting, doc, catalog]);

  const validityFor = useCallback(
    (nodeId: string, portName: string): boolean | null => {
      if (!portValidity) return null;
      return portValidity.get(`${nodeId}:${portName}`) ?? false;
    },
    [portValidity],
  );

  const handlePortDown = useCallback(
    (nodeId: string, port: string) => {
      ghostEnd.current = null;
      setConnecting({ node: nodeId, port });
    },
    [],
  );

  const handlePortUp = useCallback(
    (nodeId: string, port: string) => {
      if (!connecting) return;
      const verdict = canConnect(doc, connecting.node, connecting.port, nodeId, port, catalog);
      setConnecting(null);
      if (verdict.ok && verdict.edge) {
        onRequestConnect({
          fromNode: verdict.edge.from_node,
          fromPort: verdict.edge.from_port ?? 'out',
          toNode: verdict.edge.to_node,
          toPort: verdict.edge.to_port ?? '',
        });
      } else if (verdict.reason) {
        onConnectRejected(verdict.reason);
      }
    },
    [connecting, doc, catalog, onRequestConnect, onConnectRejected],
  );

  const handleCanvasPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!connecting) return;
      const rect = (e.currentTarget as Element).getBoundingClientRect();
      ghostEnd.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      setGhostTick((v) => v + 1);
    },
    [connecting],
  );

  const handleCanvasPointerUp = useCallback(() => {
    if (connecting) {
      setConnecting(null);
      ghostEnd.current = null;
    }
  }, [connecting]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      // 方向键在节点间移动焦点（画布级兜底：焦点落在 svg 容器时）。
      if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
      if (doc.nodes.length === 0) return;
      e.preventDefault();
      const currentIdx = doc.nodes.findIndex((n) => n.node_id === selectedNodeId);
      const step = e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 1;
      const next = doc.nodes[(currentIdx + step + doc.nodes.length) % doc.nodes.length] ?? doc.nodes[0];
      onSelectNode(next.node_id);
      // node_id 是 slug 词法（[a-z0-9_]），选择器无需转义；SVGElement 在
      // 部分 DOM 实现里无 focus 方法 —— 有则聚焦（roving），无则跳过。
      const el = (e.currentTarget as Element).querySelector(
        `[data-testid="canvas-node-${next.node_id}"]`,
      ) as { focus?: () => void } | null;
      if (el && typeof el.focus === 'function') el.focus();
    },
    [doc.nodes, selectedNodeId, onSelectNode],
  );

  if (doc.nodes.length === 0) {
    return (
      <div className="flex min-h-[220px] items-center justify-center rounded-sm border border-edge-subtle bg-surface-sunken">
        <EmptyState
          icon={Workflow}
          title={t('empty.canvasTitle')}
          description={t('empty.canvasDesc')}
        />
      </div>
    );
  }

  const width =
    CANVAS_MARGIN * 2 +
    Math.max(...doc.nodes.map((n) => (positions.get(n.node_id)?.x ?? 0) + NODE_W));
  const height =
    CANVAS_MARGIN * 2 +
    Math.max(...doc.nodes.map((n) => (positions.get(n.node_id)?.y ?? 0) + NODE_H));

  const portCenter = (nodeId: string, side: 'in' | 'out', portName?: string) => {
    const pos = positions.get(nodeId);
    if (!pos) return { x: 0, y: 0 };
    const ports = side === 'in' ? portsOf.get(nodeId)?.ins ?? [] : portsOf.get(nodeId)?.outs ?? [];
    const i = Math.max(0, ports.findIndex((p) => p.name === (portName || (side === 'in' ? 'in' : 'out'))));
    return {
      x: pos.x + (side === 'in' ? 0 : NODE_W),
      y: pos.y + NODE_H / 2 + (i - (ports.length - 1) / 2) * 14,
    };
  };

  const kindLabel = (n: ModelNode) => t(KIND_LABEL_KEY[n.kind] ?? 'node.kindOutput');

  return (
    <div className="overflow-auto rounded-sm border border-edge-subtle bg-surface-sunken">
      <svg
        role="img"
        aria-label={t('aria.canvas', { nodeCount: doc.nodes.length, edgeCount: doc.edges.length })}
        data-testid="model-canvas-svg"
        width={width}
        height={height}
        className="text-ink-secondary"
        onPointerMove={handleCanvasPointerMove}
        onPointerUp={handleCanvasPointerUp}
        onKeyDown={handleKeyDown}
        tabIndex={0}
        onClick={(e) => {
          if (e.target === e.currentTarget) onSelectNode(null);
        }}
      >
        {/* 连边层（lineage-graph 风格：bezier + currentColor + 半透明） */}
        {doc.edges.map((edge, i) => {
          const from = portCenter(edge.from_node, 'out', edge.from_port);
          const to = portCenter(edge.to_node, 'in', edge.to_port || 'in');
          const midX = (from.x + to.x) / 2;
          return (
            <path
              key={`e-${i}`}
              d={`M ${from.x} ${from.y} C ${midX} ${from.y}, ${midX} ${to.y}, ${to.x} ${to.y}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={1}
              opacity={0.5}
              className="text-status-info"
            />
          );
        })}
        {/* 连线 ghost（拖拽中虚线到指针） */}
        {connecting && ghostEnd.current && (() => {
          const from = portCenter(connecting.node, 'out', connecting.port);
          const to = ghostEnd.current;
          const midX = (from.x + to.x) / 2;
          return (
            <path
              d={`M ${from.x} ${from.y} C ${midX} ${from.y}, ${midX} ${to.y}, ${to.x} ${to.y}`}
              fill="none"
              className="stroke-status-accent"
              strokeWidth={1.5}
              strokeDasharray="5 3"
            />
          );
        })()}
        {/* 节点层 */}
        {doc.nodes.map((n) => {
          const authorId = authorNodeId(n.node_id);
          const state = nodeStates?.[authorId];
          const errCode = nodeErrors?.[authorId];
          const binding = bindings?.[authorId];
          return (
            <CanvasNode
              key={n.node_id}
              node={n}
              pos={positions.get(n.node_id) ?? { x: 0, y: 0 }}
              selected={selectedNodeId === n.node_id}
              kindLabel={kindLabel(n)}
              stateStroke={state ? NODE_STATE_STROKE[state] : undefined}
              failing={state ? isFailingNodeState(state) : false}
              errorText={errCode ? t('run.errorCode', { code: errCode }) : ''}
              bindingText={binding ? (binding.ok ? t('run.bindingOk', { ref: binding.bound_ref ?? binding.code }) : t('run.bindingFailed', { code: binding.code })) : ''}
              inputPorts={portsOf.get(n.node_id)?.ins ?? []}
              outputPorts={portsOf.get(n.node_id)?.outs ?? []}
              connecting={connecting != null}
              portValidity={validityFor(n.node_id, 'in')}
              onSelect={onSelectNode}
              onDelete={onDeleteNode}
              onMove={onMoveNode}
              onPortDown={handlePortDown}
              onPortUp={handlePortUp}
            />
          );
        })}
      </svg>
    </div>
  );
}
