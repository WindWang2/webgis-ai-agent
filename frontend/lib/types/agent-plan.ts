/**
 * Agent execution plan — backend → frontend contract from chat_engine SSE.
 *
 * Backend source of truth: app/services/chat/planner.py (Plan, PlanStep).
 * SSE events: plan_ready, plan_step_done, plan_finalized (see app/utils/sse.py).
 *
 * Note: this is distinct from `PlanProposalPayload` in app/page.tsx, which
 * is the Plan Mode (propose_plan tool) approval gate UI. The two never share
 * a Message field — `m.plan` is for proposals, `m.agentPlan` is for this.
 */

export type AgentPlanStepStatus = 'pending' | 'done' | 'skipped';

export interface AgentPlanStepState {
  n: number;
  goal: string;
  tool_family: string;
  status: AgentPlanStepStatus;
}

export interface AgentPlanState {
  intent: string;
  domains: string[];
  steps: AgentPlanStepState[];
  finalized: boolean;
}
