/**
 * ModelCanvas 画布契约：点击选中 / 拖动提交坐标 / Delete 级联删除 /
 * 连线合法性（非法目标端口着红 + onConnectRejected）/ 运行 overlay 描边。
 *
 * jsdom 没有 PointerEvent 构造器 —— 拖拽用例共用最小 polyfill
 * （复刻 components/layout/context-panel.test.tsx 的补齐）。
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { defaultTranslator } from '@/lib/i18n/translator';
import { ModelCanvas } from './model-canvas';
import { sampleDocument } from '@/test/fixtures/model-authoring-fixtures';
import type { ModelDocument, ModelNode } from '@/lib/workflow/model-graph';

beforeAll(() => {
  if (typeof window.PointerEvent !== 'function') {
    class PointerEventPolyfill extends MouseEvent {
      pointerId: number;
      pointerType: string;
      isPrimary: boolean;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
        this.pointerType = init.pointerType ?? '';
        this.isPrimary = init.isPrimary ?? false;
      }
    }
    // @ts-expect-error -- 补齐 jsdom 缺失的 DOM API 形状
    window.PointerEvent = PointerEventPolyfill;
  }
});

function node(partial: Partial<ModelNode> & { node_id: string }): ModelNode {
  return { kind: 'transform', title: partial.node_id, ...partial };
}

/** 两节点无 边文档：subject 输入 + buffer（含 layout 坐标）。 */
function twoNodeDoc(): ModelDocument {
  return {
    ...sampleDocument,
    nodes: [
      node({ node_id: 'poi_in', kind: 'data_input', role: 'subject' }),
      node({ node_id: 'buffered', kind: 'transform', transform_op: 'buffer' }),
    ],
    edges: [],
    layout: {
      poi_in: { x: 24, y: 24 },
      buffered: { x: 224, y: 24 },
    },
  };
}

function outPort(nodeId: string): Element {
  const g = screen.getByTestId(`canvas-node-${nodeId}`);
  return g.querySelector('[data-port="out"]') as Element;
}

function inPort(nodeId: string): Element {
  const g = screen.getByTestId(`canvas-node-${nodeId}`);
  return g.querySelector('[data-port="in"]') as Element;
}

describe('ModelCanvas', () => {
  it('空文档渲染 EmptyState 引导', () => {
    render(
      <ModelCanvas
        document={{ ...twoNodeDoc(), nodes: [], edges: [], layout: {} }}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        onMoveNode={vi.fn()}
        onDeleteNode={vi.fn()}
        onRequestConnect={vi.fn()}
        onConnectRejected={vi.fn()}
      />,
    );
    expect(screen.getByText(defaultTranslator('modelBuilder.empty.canvasTitle'))).toBeInTheDocument();
  });

  it('节点框渲染 kind 色条 + 标题 + 端口小圆点；aria 可达；连边层渲染', () => {
    render(<ModelCanvas document={sampleDocument} selectedNodeId={null} onSelectNode={vi.fn()} onMoveNode={vi.fn()} onDeleteNode={vi.fn()} onRequestConnect={vi.fn()} onConnectRejected={vi.fn()} />);
    const g = screen.getByTestId('canvas-node-poi_in');
    expect(g).toHaveAttribute('role', 'button');
    // aria 用 title（回退 node_id）；fixture 里 poi_in 的 title = 主体要素。
    expect(g.getAttribute('aria-label')).toContain('主体要素');
    // data_input：只有输出端口（名 = role），无输入端口。
    expect(g.querySelector('[data-port="out"]')).toHaveAttribute('data-port-name', 'subject');
    expect(g.querySelector('[data-port="in"]')).toBeNull();
    // transform：输入 + 输出端口。
    const g2 = screen.getByTestId('canvas-node-buffered');
    expect(g2.querySelector('[data-port="in"]')).not.toBeNull();
    expect(g2.querySelector('[data-port="out"]')).not.toBeNull();
    // 连边层渲染既有边（lineage 风格 bezier + 半透明 info 描边）。
    expect(document.querySelectorAll('path.text-status-info').length).toBe(sampleDocument.edges.length);
  });

  it('点击节点（pointerup 无位移）→ onSelectNode；Delete 键 → onDeleteNode', () => {
    const onSelect = vi.fn();
    const onDelete = vi.fn();
    render(<ModelCanvas document={twoNodeDoc()} selectedNodeId={null} onSelectNode={onSelect} onMoveNode={vi.fn()} onDeleteNode={onDelete} onRequestConnect={vi.fn()} onConnectRejected={vi.fn()} />);
    const g = screen.getByTestId('canvas-node-poi_in');
    fireEvent.pointerDown(g, { pointerId: 1, button: 0, clientX: 40, clientY: 40 });
    fireEvent.pointerUp(g, { pointerId: 1, clientX: 40, clientY: 40 });
    expect(onSelect).toHaveBeenCalledWith('poi_in');
    fireEvent.keyDown(screen.getByTestId('canvas-node-poi_in'), { key: 'Delete' });
    expect(onDelete).toHaveBeenCalledWith('poi_in');
  });

  it('拖动节点 pointerup 才提交坐标（拖拽期不触发 onMoveNode）', () => {
    const onMove = vi.fn();
    render(<ModelCanvas document={twoNodeDoc()} selectedNodeId={null} onSelectNode={vi.fn()} onMoveNode={onMove} onDeleteNode={vi.fn()} onRequestConnect={vi.fn()} onConnectRejected={vi.fn()} />);
    const g = screen.getByTestId('canvas-node-buffered');
    fireEvent.pointerDown(g, { pointerId: 1, button: 0, clientX: 250, clientY: 40 });
    expect(onMove).not.toHaveBeenCalled();
    fireEvent.pointerMove(g, { pointerId: 1, clientX: 300, clientY: 80 });
    expect(onMove).not.toHaveBeenCalled(); // 拖拽中只改 UI 坐标
    fireEvent.pointerUp(g, { pointerId: 1, clientX: 300, clientY: 80 });
    expect(onMove).toHaveBeenCalledWith('buffered', { x: 274, y: 64 });
  });

  it('拖动后点击（无位移）→ 选中而非提交坐标', () => {
    const onMove = vi.fn();
    const onSelect = vi.fn();
    render(<ModelCanvas document={twoNodeDoc()} selectedNodeId={null} onSelectNode={onSelect} onMoveNode={onMove} onDeleteNode={vi.fn()} onRequestConnect={vi.fn()} onConnectRejected={vi.fn()} />);
    const g = screen.getByTestId('canvas-node-buffered');
    fireEvent.pointerDown(g, { pointerId: 1, button: 0, clientX: 250, clientY: 40 });
    fireEvent.pointerUp(g, { pointerId: 1, clientX: 250, clientY: 40 });
    expect(onMove).not.toHaveBeenCalled();
    expect(onSelect).toHaveBeenCalledWith('buffered');
  });

  it('合法连线：out 端口按下 → in 端口松手 → onRequestConnect（from_port = role）', () => {
    const onRequest = vi.fn();
    const onReject = vi.fn();
    render(<ModelCanvas document={twoNodeDoc()} selectedNodeId={null} onSelectNode={vi.fn()} onMoveNode={vi.fn()} onDeleteNode={vi.fn()} onRequestConnect={onRequest} onConnectRejected={onReject} />);
    fireEvent.pointerDown(outPort('poi_in'), { pointerId: 1, button: 0 });
    fireEvent.pointerUp(inPort('buffered'), { pointerId: 1 });
    expect(onRequest).toHaveBeenCalledWith({ fromNode: 'poi_in', fromPort: 'subject', toNode: 'buffered', toPort: '' });
    expect(onReject).not.toHaveBeenCalled();
  });

  it('非法连线：拖拽中非法目标端口着红；松手 → onConnectRejected(typeMismatch)', () => {
    const onRequest = vi.fn();
    const onReject = vi.fn();
    // measure 表 → 钉定 point_feature_set 的能力 = 类型不匹配。
    const doc = twoNodeDoc();
    doc.nodes = [
      node({ node_id: 'tbl', kind: 'data_input', role: 'measure' }),
      node({ node_id: 'cap', kind: 'analysis', capability: 'cap.density' }),
    ];
    doc.layout = { tbl: { x: 24, y: 24 }, cap: { x: 224, y: 24 } };
    render(
      <ModelCanvas
        document={doc}
        selectedNodeId={null}
        catalog={{ 'cap.density': { input_artifact_types: ['point_feature_set'], output_artifact_types: ['density_surface'] } }}
        onSelectNode={vi.fn()}
        onMoveNode={vi.fn()}
        onDeleteNode={vi.fn()}
        onRequestConnect={onRequest}
        onConnectRejected={onReject}
      />,
    );
    fireEvent.pointerDown(outPort('tbl'), { pointerId: 1, button: 0 });
    // 拖拽中：非法目标端口着红（status-critical）。
    expect(inPort('cap').classList.contains('fill-status-critical')).toBe(true);
    fireEvent.pointerUp(inPort('cap'), { pointerId: 1 });
    expect(onRequest).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith('typeMismatch');
  });

  it('运行 overlay：节点态映射描边色；FAILED 附错误徽标', () => {
    const doc = twoNodeDoc();
    doc.nodes = [
      node({ node_id: 'poi_in', kind: 'data_input', role: 'subject' }),
      node({ node_id: 'buffered', kind: 'transform', transform_op: 'buffer' }),
    ];
    render(
      <ModelCanvas
        document={doc}
        selectedNodeId={null}
        nodeStates={{ poi_in: 'SUCCEEDED', buffered: 'FAILED' }}
        nodeErrors={{ buffered: 'GEOM_INVALID' }}
        onSelectNode={vi.fn()}
        onMoveNode={vi.fn()}
        onDeleteNode={vi.fn()}
        onRequestConnect={vi.fn()}
        onConnectRejected={vi.fn()}
      />,
    );
    expect(screen.getByTestId('canvas-node-poi_in').querySelector('rect')?.classList.contains('stroke-status-success')).toBe(true);
    const failed = screen.getByTestId('canvas-node-buffered');
    expect(failed.querySelector('rect')?.classList.contains('stroke-status-critical')).toBe(true);
    // 失败徽标（红点）+ aria 携带 error_code。
    expect(failed.querySelector('circle.fill-status-critical')).not.toBeNull();
    expect(failed.getAttribute('aria-label')).toContain('GEOM_INVALID');
  });

  it('方向键在节点间移动焦点（画布级 roving；selection 回灌 props）', () => {
    const onSelect = vi.fn();
    function Harness() {
      const [selected, setSelected] = useState<string | null>('poi_in');
      return (
        <ModelCanvas
          document={twoNodeDoc()}
          selectedNodeId={selected}
          onSelectNode={(id) => {
            onSelect(id);
            setSelected(id);
          }}
          onMoveNode={vi.fn()}
          onDeleteNode={vi.fn()}
          onRequestConnect={vi.fn()}
          onConnectRejected={vi.fn()}
        />
      );
    }
    render(<Harness />);
    const svg = screen.getByTestId('model-canvas-svg');
    fireEvent.keyDown(svg, { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenLastCalledWith('buffered');
    fireEvent.keyDown(svg, { key: 'ArrowDown' });
    expect(onSelect).toHaveBeenLastCalledWith('poi_in');
  });
});
