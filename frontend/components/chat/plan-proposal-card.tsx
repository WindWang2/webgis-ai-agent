'use client';

/**
 * PlanProposalCard — Plan Mode 审批卡片
 *
 * 渲染条件：消息上挂载了 plan 字段（来自 propose_plan 工具结果）。
 * 行为：
 *  - 默认 status='pending' 展示步骤列表 + 「执行 / 修改 / 取消」三按钮；
 *  - 用户点击「执行」→ 父组件通过 onApprove(plan_id) 触发一条 chat 消息让 LLM 调 execute_plan；
 *  - 「修改」/「取消」类似，分别发送让 LLM 改计划/放弃的指令；
 *  - 「执行」/「取消」后切到 approved/rejected 锁住卡片；「修改」切到
 *    revising（修改中，非终态，按钮仍可点）。
 */

import { useState } from 'react';
import { CheckCircle2, AlertTriangle, Play, X, Edit3, Lock, ListTodo } from 'lucide-react';
import type { PlanProposalStatus } from '@/lib/store/hud-types';

export interface PlanStepPreview {
  id: string;
  tool: string;
  purpose?: string;
  destructive?: boolean;
}

export interface PlanProposalCardProps {
  planId: string;
  title: string;
  summary?: string;
  stepCount: number;
  destructiveSteps?: string[];
  stepsPreview?: PlanStepPreview[];
  status: PlanProposalStatus;
  /** 执行确认 — 父组件应触发一条 chat 让 LLM 调 execute_plan(plan_id)。 */
  onApprove: (planId: string) => void;
  /** 让 LLM 修改计划 */
  onRevise: (planId: string) => void;
  /** 取消计划 */
  onReject: (planId: string) => void;
}

export function PlanProposalCard(props: PlanProposalCardProps) {
  const {
    planId,
    title,
    summary,
    stepCount,
    destructiveSteps = [],
    stepsPreview = [],
    status,
    onApprove,
    onRevise,
    onReject,
  } = props;

  const [expanded, setExpanded] = useState(true);
  const hasDestructive = destructiveSteps.length > 0;
  const locked = status === 'approved' || status === 'rejected';

  /* V4（D）：卡片表面/文字全部走语义 token（随主题翻转），不再按 isDark 手工
     二选一 —— 原来暗色下的 subText #94a3b8 只有 2.45:1。accent 作文字一律用
     text-safe 派生（--agent-accent 已含主题校正）。 */
  return (
    <div
      data-testid="plan-proposal-card"
      className="my-2 rounded-md border border-edge-subtle bg-surface-raised p-3 text-body"
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          style={{ backgroundColor: 'color-mix(in srgb, var(--agent-accent) 12%, transparent)' }}
        >
          <ListTodo size={14} style={{ color: 'var(--agent-accent)' }} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-title font-semibold text-ink">{title}</span>
            <span
              className="rounded-pill px-2 py-px text-meta font-medium"
              style={{
                color: 'var(--agent-accent)',
                backgroundColor: 'color-mix(in srgb, var(--agent-accent) 12%, transparent)',
              }}
            >
              计划 · {stepCount} 步
            </span>
            {status === 'approved' && (
              <span className="inline-flex items-center gap-1 rounded-pill bg-status-success-soft px-2 py-px text-meta text-status-success">
                <CheckCircle2 size={10} /> 已批准
              </span>
            )}
            {status === 'rejected' && (
              <span className="inline-flex items-center gap-1 rounded-pill bg-status-critical-soft px-2 py-px text-meta text-status-critical">
                <X size={10} /> 已取消
              </span>
            )}
            {status === 'revising' && (
              <span className="inline-flex items-center gap-1 rounded-pill bg-status-info-soft px-2 py-px text-meta text-status-info">
                <Edit3 size={10} /> 修改中
              </span>
            )}
          </div>
          {summary && (
            <div className="mt-1 text-body text-ink-muted">{summary}</div>
          )}
        </div>
      </div>

      {/* Destructive warning */}
      {hasDestructive && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md border border-status-warning-border bg-status-warning-soft p-2 text-body leading-relaxed text-status-warning">
          <AlertTriangle size={12} className="mt-[1px] shrink-0" />
          <span>
            本计划含 {destructiveSteps.length} 个破坏性步骤（{destructiveSteps.join('、')}），
            执行前请确认这些操作可逆且经过授权。
          </span>
        </div>
      )}

      {/* Steps */}
      {stepsPreview.length > 0 && (
        <div className="mt-2.5">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="cursor-pointer border-none bg-transparent p-0 text-meta text-ink-muted"
          >
            {expanded ? '▾' : '▸'} 步骤明细
          </button>
          {expanded && (
            <ol className="mt-1.5 flex list-none flex-col gap-1 p-0">
              {stepsPreview.map((step, i) => (
                <li
                  key={step.id}
                  className={`flex gap-2 rounded-md px-2 py-1 ${
                    step.destructive ? 'bg-status-warning-soft' : 'bg-surface-sunken'
                  }`}
                >
                  <span className="min-w-[18px] font-semibold text-ink-muted">{i + 1}.</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-ink">
                      <code className="text-meta text-agent-accent">{step.tool}</code>
                      {step.destructive && (
                        <span className="ml-1.5 text-meta text-status-warning">
                          ⚠ 破坏性
                        </span>
                      )}
                    </div>
                    {step.purpose && (
                      <div className="text-meta text-ink-muted">{step.purpose}</div>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {/* Action buttons */}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={locked}
          onClick={() => onApprove(planId)}
          className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-body font-medium ${
            locked ? 'cursor-not-allowed' : 'cursor-pointer'
          }`}
          style={{
            backgroundColor: locked ? 'color-mix(in srgb, var(--neutral) 30%, transparent)' : 'var(--agent-accent)',
            color: locked ? 'var(--text-muted)' : 'var(--text-on-accent)',
          }}
        >
          {locked ? <Lock size={12} /> : <Play size={12} />}
          {status === 'approved' ? '已批准' : '执行计划'}
        </button>
        <button
          type="button"
          disabled={locked}
          onClick={() => onRevise(planId)}
          className={`flex items-center gap-1.5 rounded-md border border-edge-subtle bg-transparent px-2.5 py-1.5 text-body ${
            locked ? 'cursor-not-allowed' : 'cursor-pointer'
          }`}
          style={{
            color: locked ? 'var(--text-muted)' : 'var(--text-primary)',
          }}
        >
          <Edit3 size={12} /> 修改
        </button>
        <button
          type="button"
          disabled={locked}
          onClick={() => onReject(planId)}
          className={`flex items-center gap-1.5 rounded-md border border-edge-subtle bg-transparent px-2.5 py-1.5 text-body ${
            locked ? 'cursor-not-allowed' : 'cursor-pointer'
          }`}
          style={{
            color: locked ? 'var(--text-muted)' : 'var(--text-critical)',
          }}
        >
          <X size={12} /> 取消
        </button>
      </div>
    </div>
  );
}
