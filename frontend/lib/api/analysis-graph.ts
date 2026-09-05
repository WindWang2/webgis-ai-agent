/**
 * Analysis Graph API client（ADR-0097 显式分析图 — 只读派生投影）。
 *
 * GET /api/v1/sessions/{sid}/analysis-graph：goal（目标 + 方法论警告）+
 * execution DAG（capability 依赖/状态/算法/工具）+ product facets（完成度
 * + 重算维度）+ next_action（统一下一动作）。零持久化 —— 每次读都是投影。
 */
import { apiFetch } from './transport';

export type AnalysisNodeKind = 'goal' | 'requirement' | 'analysis' | 'product';

export type ExecutionNodeStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'complete'
  | 'skipped'
  | 'unavailable'
  | 'failed';

export type ProductFacetStatus =
  | 'complete'
  | 'pending'
  | 'failed'
  | 'needs_repair'
  | 'off';

export interface MethodologyWarningView {
  pattern: string;
  code: string;
  missing_roles: string[];
  disclosures: string[];
}

export interface GoalNode {
  id: 'goal';
  kind: 'goal';
  label: string;
  query: string;
  recipe_id: string;
  plan_id: string;
  status: string;
  superseded: boolean;
  replaced: boolean;
  methodology_warnings: MethodologyWarningView[];
}

export interface ExecutionNode {
  id: string;
  kind: 'requirement' | 'analysis';
  capability: string;
  purpose: string;
  status: ExecutionNodeStatus;
  algorithm: string;
  tool: string;
  depends_on: string[];
  bound_ref: string;
  input_refs: string[];
  optional: boolean;
  cost_class: string;
  fallback_to: string;
  blocked_by: string[];
  notes: string[];
  recompute_impact: string;
}

export interface ProductFacetNode {
  id: string;
  kind: 'product';
  facet_kind: string;
  label: string;
  status: ProductFacetStatus;
  required: boolean;
  capabilities: string[];
  artifact_ref: string;
  layer_ids: string[];
  component_ids: string[];
  dependencies: string[];
  render_status: string;
  recompute_dims: string[];
}

export interface NextAction {
  facet_id: string;
  kind: string;
  action: string;
  reason: string;
  capability: string;
  mode: string;
  class: string;
}

export interface AnalysisGraph {
  session_id: string;
  envelope_id: string;
  goal: GoalNode | null;
  nodes: Array<GoalNode | ExecutionNode | ProductFacetNode>;
  counts: { goal: number; execution: number; product: number };
  next_action: NextAction | null;
  notes: string[];
}

export async function getAnalysisGraph(
  sessionId: string,
  ownerToken?: string | null,
): Promise<AnalysisGraph | null> {
  try {
    return apiFetch<AnalysisGraph>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/analysis-graph`,
      { ownerToken, label: 'AnalysisGraph' },
    );
  } catch {
    // 投影端点失败按缺席处理（面板隐藏/空态），绝不阻塞工作区
    return null;
  }
}
