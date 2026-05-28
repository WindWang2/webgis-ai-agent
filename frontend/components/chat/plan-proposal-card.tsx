'use client';

/**
 * PlanProposalCard — Plan Mode 审批卡片
 *
 * 渲染条件：消息上挂载了 plan 字段（来自 propose_plan 工具结果）。
 * 行为：
 *  - 默认 status='pending' 展示步骤列表 + 「执行 / 修改 / 取消」三按钮；
 *  - 用户点击「执行」→ 父组件通过 onApprove(plan_id) 触发一条 chat 消息让 LLM 调 execute_plan；
 *  - 「修改」/「取消」类似，分别发送让 LLM 改计划/放弃的指令；
 *  - 点击后切换到 status='approved'|'rejected' 锁住卡片，避免重复点击。
 */

import { useState } from 'react';
import { CheckCircle2, AlertTriangle, Play, X, Edit3, Lock, ListTodo } from 'lucide-react';

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
  status: 'pending' | 'approved' | 'rejected';
  isDark?: boolean;
  accentColor?: string;
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
    isDark = true,
    accentColor = '#10b981',
    onApprove,
    onRevise,
    onReject,
  } = props;

  const [expanded, setExpanded] = useState(true);
  const hasDestructive = destructiveSteps.length > 0;
  const locked = status !== 'pending';

  const bg = isDark ? 'rgba(15,23,42,0.6)' : 'rgba(248,250,252,0.95)';
  const border = isDark ? 'rgba(148,163,184,0.25)' : 'rgba(203,213,225,0.9)';
  const subText = isDark ? '#94a3b8' : '#64748b';
  const titleColor = isDark ? '#e2e8f0' : '#0f172a';

  return (
    <div
      data-testid="plan-proposal-card"
      style={{
        marginTop: 8,
        borderRadius: 12,
        border: `1px solid ${border}`,
        backgroundColor: bg,
        padding: 12,
        fontSize: 14,
      }}
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <div
          className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
          style={{ backgroundColor: `${accentColor}1f` }}
        >
          <ListTodo size={14} style={{ color: accentColor }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span style={{ color: titleColor, fontWeight: 600, fontSize: 15 }}>{title}</span>
            <span
              style={{
                padding: '1px 8px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 500,
                color: accentColor,
                backgroundColor: `${accentColor}1f`,
              }}
            >
              计划 · {stepCount} 步
            </span>
            {status === 'approved' && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '1px 8px',
                  borderRadius: 999,
                  fontSize: 12,
                  color: '#22c55e',
                  backgroundColor: 'rgba(34,197,94,0.15)',
                }}
              >
                <CheckCircle2 size={10} /> 已批准
              </span>
            )}
            {status === 'rejected' && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '1px 8px',
                  borderRadius: 999,
                  fontSize: 12,
                  color: '#ef4444',
                  backgroundColor: 'rgba(239,68,68,0.15)',
                }}
              >
                <X size={10} /> 已取消
              </span>
            )}
          </div>
          {summary && (
            <div style={{ color: subText, marginTop: 4, lineHeight: 1.5 }}>{summary}</div>
          )}
        </div>
      </div>

      {/* Destructive warning */}
      {hasDestructive && (
        <div
          style={{
            marginTop: 8,
            padding: '6px 10px',
            borderRadius: 6,
            backgroundColor: 'rgba(245,158,11,0.12)',
            border: '1px solid rgba(245,158,11,0.3)',
            color: '#f59e0b',
            display: 'flex',
            gap: 6,
            alignItems: 'flex-start',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          <AlertTriangle size={12} className="mt-[1px] shrink-0" />
          <span>
            本计划含 {destructiveSteps.length} 个破坏性步骤（{destructiveSteps.join('、')}），
            执行前请确认这些操作可逆且经过授权。
          </span>
        </div>
      )}

      {/* Steps */}
      {stepsPreview.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            style={{
              color: subText,
              fontSize: 12,
              padding: 0,
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            {expanded ? '▾' : '▸'} 步骤明细
          </button>
          {expanded && (
            <ol
              style={{
                marginTop: 6,
                paddingLeft: 0,
                listStyle: 'none',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}
            >
              {stepsPreview.map((step, i) => (
                <li
                  key={step.id}
                  style={{
                    display: 'flex',
                    gap: 8,
                    padding: '4px 8px',
                    borderRadius: 6,
                    backgroundColor: step.destructive
                      ? 'rgba(245,158,11,0.08)'
                      : isDark
                      ? 'rgba(30,41,59,0.4)'
                      : 'rgba(241,245,249,0.6)',
                    fontSize: 13,
                    lineHeight: 1.5,
                  }}
                >
                  <span style={{ color: subText, fontWeight: 600, minWidth: 18 }}>{i + 1}.</span>
                  <div className="flex-1 min-w-0">
                    <div style={{ color: titleColor }}>
                      <code style={{ fontSize: 12, color: accentColor }}>{step.tool}</code>
                      {step.destructive && (
                        <span style={{ marginLeft: 6, color: '#f59e0b', fontSize: 12 }}>
                          ⚠ 破坏性
                        </span>
                      )}
                    </div>
                    {step.purpose && (
                      <div style={{ color: subText, fontSize: 12.5 }}>{step.purpose}</div>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-2" style={{ marginTop: 12 }}>
        <button
          type="button"
          disabled={locked}
          onClick={() => onApprove(planId)}
          style={{
            flex: 1,
            padding: '6px 10px',
            borderRadius: 8,
            border: 'none',
            backgroundColor: locked ? 'rgba(100,116,139,0.3)' : accentColor,
            color: locked ? subText : '#fff',
            fontSize: 14,
            fontWeight: 500,
            cursor: locked ? 'not-allowed' : 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
          }}
        >
          {locked ? <Lock size={12} /> : <Play size={12} />}
          {status === 'approved' ? '已批准' : '执行计划'}
        </button>
        <button
          type="button"
          disabled={locked}
          onClick={() => onRevise(planId)}
          style={{
            padding: '6px 10px',
            borderRadius: 8,
            border: `1px solid ${border}`,
            backgroundColor: 'transparent',
            color: locked ? subText : titleColor,
            fontSize: 14,
            cursor: locked ? 'not-allowed' : 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <Edit3 size={12} /> 修改
        </button>
        <button
          type="button"
          disabled={locked}
          onClick={() => onReject(planId)}
          style={{
            padding: '6px 10px',
            borderRadius: 8,
            border: `1px solid ${border}`,
            backgroundColor: 'transparent',
            color: locked ? subText : '#ef4444',
            fontSize: 14,
            cursor: locked ? 'not-allowed' : 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <X size={12} /> 取消
        </button>
      </div>
    </div>
  );
}
