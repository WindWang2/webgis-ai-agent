'use client';

import type { ReactElement } from 'react';
import { ClipboardList, Check, Circle, MinusCircle } from 'lucide-react';
import type { AgentPlanState, AgentPlanStepStatus } from '@/lib/types/agent-plan';

interface Props {
  plan: AgentPlanState;
}

const STATUS_ICON: Record<AgentPlanStepStatus, ReactElement> = {
  done: <Check className="h-3 w-3 text-emerald-500" />,
  pending: <Circle className="h-3 w-3 text-muted-foreground/50 animate-pulse" />,
  skipped: <MinusCircle className="h-3 w-3 text-muted-foreground/40" />,
};

export function PlanCard({ plan }: Props) {
  const total = plan.steps.length;
  if (total === 0) return null;
  const doneCount = plan.steps.filter(s => s.status === 'done').length;
  return (
    <div className="my-2 p-3 rounded-lg border border-border bg-card/60">
      <div className="flex items-center gap-2 mb-2">
        <ClipboardList className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold text-foreground truncate">{plan.intent}</span>
        <span className="text-[10px] ml-auto text-muted-foreground tabular-nums">
          {doneCount} / {total}
        </span>
      </div>
      <ul className="space-y-1">
        {plan.steps.map(s => (
          <li
            key={s.n}
            className={`flex items-center gap-2 text-[11px] ${
              s.status === 'skipped' ? 'opacity-50' : ''
            }`}
          >
            <span className="shrink-0">{STATUS_ICON[s.status]}</span>
            <span className={`flex-1 ${s.status === 'done' ? 'text-foreground' : 'text-muted-foreground'}`}>
              {s.goal}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
