'use client';

import type { ReactElement } from 'react';
import { ClipboardList, Check, Circle, MinusCircle } from 'lucide-react';
import type { AgentPlanState, AgentPlanStepStatus } from '@/lib/types/agent-plan';

interface Props {
  plan: AgentPlanState;
}

const STATUS_ICON: Record<AgentPlanStepStatus, ReactElement> = {
  done: <Check className="h-3 w-3 text-status-success" />,
  pending: <Circle className="h-3 w-3 text-ink-disabled animate-pulse" />,
  skipped: <MinusCircle className="h-3 w-3 text-ink-disabled" />,
};

export function PlanCard({ plan }: Props) {
  const total = plan.steps.length;
  if (total === 0) return null;
  const doneCount = plan.steps.filter(s => s.status === 'done').length;
  return (
    <div className="my-2 p-3 rounded-md border border-edge-subtle bg-surface-raised">
      <div className="flex items-center gap-2 mb-2">
        <ClipboardList className="h-4 w-4 text-status-accent" />
        <span className="text-body font-semibold text-ink truncate">{plan.intent}</span>
        <span className="text-body ml-auto text-ink-muted tabular-nums">
          {doneCount} / {total}
        </span>
      </div>
      <ul className="space-y-1">
        {plan.steps.map(s => (
          <li
            key={s.n}
            className={`flex items-center gap-2 text-body ${
              s.status === 'skipped' ? 'opacity-50' : ''
            }`}
          >
            <span className="shrink-0">{STATUS_ICON[s.status]}</span>
            <span className={`flex-1 ${s.status === 'done' ? 'text-ink' : 'text-ink-muted'}`}>
              {s.goal}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
