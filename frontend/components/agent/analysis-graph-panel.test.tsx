/**
 * 显式分析图面板（ADR-0097）回归：挂载水合、目标/警告/DAG/facets/下一动作
 * 渲染、空态与端点失败隐藏。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AnalysisGraphPanel } from './analysis-graph-panel';
import * as api from '@/lib/api/analysis-graph';
import type { AnalysisGraph } from '@/lib/api/analysis-graph';

vi.mock('@/lib/api/analysis-graph', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/analysis-graph')>()),
}));

function makeGraph(overrides: Partial<AnalysisGraph> = {}): AnalysisGraph {
  return {
    session_id: 's1',
    envelope_id: 'sp-1',
    goal: {
      id: 'goal',
      kind: 'goal',
      label: '为成都新分校选址推荐最优位置',
      query: '选址',
      recipe_id: 'site_selection',
      plan_id: 'plan-1',
      status: 'draft',
      superseded: false,
      replaced: false,
      methodology_warnings: [
        {
          pattern: 'site_selection',
          code: 'SITE_SELECTION_CRITERIA_UNDECLARED',
          missing_roles: [],
          disclosures: ['准则与权重尚未声明：MCDA 评价前必须由用户确认'],
        },
      ],
    },
    nodes: [
      {
        id: 'poi_query',
        kind: 'requirement',
        capability: 'poi_query',
        purpose: '学校要素获取',
        status: 'complete',
        algorithm: 'poi.query.local',
        tool: 'query_local_poi',
        depends_on: [],
        bound_ref: 'ref:geojson-1',
        input_refs: [],
        optional: false,
        cost_class: '',
        fallback_to: '',
        blocked_by: [],
        notes: [],
        recompute_impact: 'downstream',
      },
      {
        id: 'mcda_evaluation',
        kind: 'analysis',
        capability: 'mcda_evaluation',
        purpose: '多准则决策评价（MCDA）',
        status: 'ready',
        algorithm: 'decision.mcda.wsm',
        tool: 'spatial_decision_v3',
        depends_on: ['poi_query'],
        bound_ref: '',
        input_refs: ['ref:geojson-1'],
        optional: false,
        cost_class: '',
        fallback_to: '',
        blocked_by: [],
        notes: [],
        recompute_impact: 'downstream',
      },
      {
        id: 'map:primary',
        kind: 'product',
        facet_kind: 'map_layer',
        label: '主图层',
        status: 'pending',
        required: true,
        capabilities: ['poi_query'],
        artifact_ref: '',
        layer_ids: [],
        component_ids: [],
        dependencies: [],
        render_status: '',
        recompute_dims: ['data', 'algorithm', 'parameter', 'style', 'output'],
      },
    ],
    counts: { goal: 1, execution: 2, product: 1 },
    next_action: {
      facet_id: 'analysis:mcda_evaluation',
      kind: 'analysis',
      action: 'run_capability',
      reason: "capability 'mcda_evaluation' ready — run with bound inputs",
      capability: 'mcda_evaluation',
      mode: 'capability',
      class: 'execution_debt',
    },
    notes: [],
    ...overrides,
  };
}

describe('AnalysisGraphPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders goal, methodology warnings, DAG, facets and next action', async () => {
    vi.spyOn(api, 'getAnalysisGraph').mockResolvedValue(makeGraph());
    render(<AnalysisGraphPanel sessionId="s1" />);

    await waitFor(() => {
      expect(screen.getByTestId('analysis-graph-panel')).toBeTruthy();
    });
    expect(screen.getByText(/为成都新分校选址推荐最优位置/)).toBeTruthy();
    expect(screen.getByTestId('analysis-graph-warnings').textContent).toContain(
      'SITE_SELECTION_CRITERIA_UNDECLARED',
    );
    expect(screen.getByTestId('analysis-graph-next-action').textContent).toContain(
      'mcda_evaluation',
    );
    // 执行步骤展开（open）渲染节点行
    expect(screen.getByText(/学校要素获取/)).toBeTruthy();
    expect(screen.getByText(/多准则决策评价/)).toBeTruthy();
  });

  it('renders honest empty state when no plan chapter exists', async () => {
    vi.spyOn(api, 'getAnalysisGraph').mockResolvedValue(
      makeGraph({ goal: null, nodes: [], next_action: null, notes: ['no session plan chapter'] }),
    );
    render(<AnalysisGraphPanel sessionId="s1" />);
    await waitFor(() => {
      expect(screen.getByText(/暂无会话计划/)).toBeTruthy();
    });
  });

  it('hides entirely when the endpoint fails', async () => {
    vi.spyOn(api, 'getAnalysisGraph').mockResolvedValue(null);
    const { container } = render(<AnalysisGraphPanel sessionId="s1" />);
    await waitFor(() => {
      expect(container.querySelector('[data-testid="analysis-graph-panel"]')).toBeNull();
    });
  });

  it('renders nothing without a session', () => {
    const { container } = render(<AnalysisGraphPanel sessionId={null} />);
    expect(container.querySelector('[data-testid="analysis-graph-panel"]')).toBeNull();
  });
});
