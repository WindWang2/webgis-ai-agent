'use client'

/**
 * VisualRepairCard — C13 视觉修复审批/预览/diff 卡片。
 *
 * 自包含流程：用户点开 → POST plan（零突变预览）→ 结构化 ops diff
 * （改哪些层/什么呈现属性 + touches_locked 知情披露）→ 批准（apply，
 * approved=true + expected_revision CAS）/ 拒绝（reject，拒绝记忆入账）。
 *
 * 纪律：
 * - **不展示内部推理**：只渲染服务端派生的结构化 ops（op/层/severity/
 *   rationale 摘要），没有链路思维、没有模型输出原文；
 * - **user-wins**：批准是显式动作（approved=true 结构门槛）；拒绝后
 *   同一缺陷不再被提示（后端拒绝记忆）；
 * - 全部文案走 i18n（G13 CJK 门禁）。
 */

import { useCallback, useEffect, useState } from 'react'
import { Eye, Loader2, ShieldCheck, X, Check, AlertTriangle, Lock } from 'lucide-react'
import { apiFetch } from '@/lib/api/transport'
import { useT } from '@/lib/i18n/useT'
import { devOnly } from '@/lib/utils/logger'

export interface VisualRepairOpPreview {
  op: string
  layer_ids?: string[]
  severity?: string
  rationale?: string
  touches_locked?: boolean
}

export interface VisualRepairPlanResponse {
  proposable?: boolean
  reason?: string
  proposal_id?: string
  base_revision?: number
  ops?: VisualRepairOpPreview[]
  skipped?: Array<{ reason?: string }>
  stale_count?: number
}

export interface VisualRepairApplyResponse {
  applied?: boolean
  duplicate?: boolean
  reason?: string
  hard_stop?: boolean
  mutation_revision?: number
}

type CardPhase =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'plan'; plan: VisualRepairPlanResponse }
  | { kind: 'applying'; plan: VisualRepairPlanResponse }
  | { kind: 'applied'; duplicate: boolean }
  | { kind: 'hard_stop' }
  | { kind: 'apply_failed'; reason: string }
  | { kind: 'error' }

const OP_LABEL_KEYS: Record<string, string> = {
  label_layout: 'opLabelLayout',
  contrast_palette: 'opContrastPalette',
  layer_order: 'opLayerOrder',
  opacity: 'opOpacity',
}

const REASON_KEYS: Record<string, string> = {
  no_visual_findings: 'reasonNoVisualFindings',
  stale_findings: 'reasonStaleFindings',
  no_healable_defects: 'reasonNoHealableDefects',
  no_safe_ops: 'reasonNoSafeOps',
  all_rejected_by_user: 'reasonAllRejected',
  mapspec_unavailable: 'reasonMapspecUnavailable',
}

export function VisualRepairCard(props: {
  sessionId?: string | null
  ownerToken?: string | null
}) {
  const { sessionId, ownerToken } = props
  const t = useT()
  const [open, setOpen] = useState(false)
  const [phase, setPhase] = useState<CardPhase>({ kind: 'idle' })
  // 会话切换即重置 —— 旧会话的展开态/plan/proposal_id 不得带入新会话
  // （旧 proposal 打到新会话只会得到 404，安全但困惑；C13 review P3-4）。
  useEffect(() => {
    setOpen(false)
    setPhase({ kind: 'idle' })
  }, [sessionId])
  // rules-of-hooks：guard 在全部 hook 之后（渲染面兜底 sessionId 缺席）。
  const base = sessionId
    ? `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/visual-repairs`
    : ''

  const buildPlan = useCallback(async () => {
    if (!sessionId) return
    setPhase({ kind: 'checking' })
    try {
      const plan = await apiFetch<VisualRepairPlanResponse>(`${base}/plan`, {
        method: 'POST',
        body: {},
        ownerToken,
        label: 'Visual repair plan error',
      })
      setPhase({ kind: 'plan', plan })
    } catch (error) {
      devOnly.warn('[visual-repair-card] plan failed', error)
      setPhase({ kind: 'error' })
    }
  }, [base, ownerToken, sessionId])

  const approve = useCallback(async (plan: VisualRepairPlanResponse) => {
    if (!plan.proposal_id) return
    setPhase({ kind: 'applying', plan })
    try {
      const receipt = await apiFetch<VisualRepairApplyResponse>(
        `${base}/apply`,
        {
          method: 'POST',
          body: {
            proposal_id: plan.proposal_id,
            approved: true,
            expected_revision: plan.base_revision ?? 0,
          },
          ownerToken,
          label: 'Visual repair apply error',
        },
      )
      if (receipt.applied) {
        setPhase({ kind: 'applied', duplicate: Boolean(receipt.duplicate) })
      } else if (receipt.hard_stop) {
        setPhase({ kind: 'hard_stop' })
      } else {
        setPhase({ kind: 'apply_failed', reason: receipt.reason || '' })
      }
    } catch (error) {
      devOnly.warn('[visual-repair-card] apply failed', error)
      setPhase({ kind: 'error' })
    }
  }, [base, ownerToken])

  const reject = useCallback(async (plan: VisualRepairPlanResponse) => {
    if (!plan.proposal_id) return
    try {
      await apiFetch(`${base}/reject`, {
        method: 'POST',
        body: { proposal_id: plan.proposal_id },
        ownerToken,
        label: 'Visual repair reject error',
      })
    } catch (error) {
      // 拒绝回执失败不打断用户 —— 下次 plan 会重新提案（保守方向）。
      devOnly.warn('[visual-repair-card] reject failed', error)
    }
    setPhase({ kind: 'idle' })
    setOpen(false)
  }, [base, ownerToken])

  if (!sessionId) return null

  if (!open) {
    return (
      <button
        type="button"
        data-testid="visual-repair-open"
        title={t('map.visualRepair.openTitle')}
        onClick={() => {
          setOpen(true)
          void buildPlan()
        }}
        className="pointer-events-auto absolute bottom-28 left-3 z-40 flex items-center gap-1.5 rounded-md border border-edge-subtle bg-surface-raised/95 px-2.5 py-1.5 text-micro font-medium text-ink shadow-agent-md hover:bg-surface-overlay"
      >
        <Eye className="h-3.5 w-3.5" aria-hidden />
        {t('map.visualRepair.open')}
      </button>
    )
  }

  const plan = phase.kind === 'plan' || phase.kind === 'applying' ? phase.plan : null
  const busy = phase.kind === 'checking' || phase.kind === 'applying'
  const nonProposable = phase.kind === 'plan' && !phase.plan.proposable
  const reasonKey = nonProposable
    ? (REASON_KEYS[phase.plan.reason || ''] ?? 'reasonGeneric')
    : ''

  return (
    <div
      data-testid="visual-repair-card"
      role="dialog"
      aria-label={t('map.visualRepair.proposalTitle')}
      className="pointer-events-auto absolute bottom-28 left-3 z-40 w-80 rounded-md border border-edge-subtle bg-surface-raised p-3 text-body shadow-agent-md"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-caption font-semibold text-ink">
          <ShieldCheck className="h-4 w-4" aria-hidden />
          {t('map.visualRepair.proposalTitle')}
        </span>
        <button
          type="button"
          data-testid="visual-repair-dismiss"
          aria-label={t('map.visualRepair.dismiss')}
          disabled={busy}
          onClick={() => {
            setOpen(false)
            setPhase({ kind: 'idle' })
          }}
          className="rounded p-0.5 text-ink-secondary hover:bg-surface-overlay"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {phase.kind === 'checking' && (
        <p className="flex items-center gap-2 text-caption text-ink-secondary" data-testid="visual-repair-checking">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {t('map.visualRepair.checking')}
        </p>
      )}

      {phase.kind === 'error' && (
        <p className="text-caption text-status-critical" data-testid="visual-repair-error">
          {t('map.visualRepair.requestFailed')}
        </p>
      )}

      {nonProposable && (
        <div data-testid="visual-repair-nonproposable">
          <p className="text-caption text-ink-secondary">{t(`map.visualRepair.${reasonKey}`)}</p>
          {typeof phase.plan.stale_count === 'number' && phase.plan.stale_count > 0 && (
            <p className="mt-1 text-micro text-ink-muted">
              {t('map.visualRepair.staleCount', { count: phase.plan.stale_count })}
            </p>
          )}
          <button
            type="button"
            className="mt-2 rounded border border-edge-subtle px-2 py-1 text-micro text-ink hover:bg-surface-overlay"
            onClick={() => void buildPlan()}
          >
            {t('map.visualRepair.recheck')}
          </button>
        </div>
      )}

      {plan?.proposable && (
        <>
          <p className="mb-2 text-micro text-ink-secondary">{t('map.visualRepair.proposalHint')}</p>
          <ul className="mb-2 space-y-1.5" data-testid="visual-repair-ops">
            {(plan.ops ?? []).map((op, i) => (
              <li
                key={`${op.op}-${i}`}
                data-testid="visual-repair-op"
                className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-caption font-medium text-ink">
                    {OP_LABEL_KEYS[op.op]
                      ? t(`map.visualRepair.${OP_LABEL_KEYS[op.op]}`)
                      : op.op}
                  </span>
                  {op.touches_locked && (
                    <span
                      data-testid="visual-repair-touches-locked"
                      className="flex items-center gap-1 rounded bg-status-warning/15 px-1.5 py-0.5 text-micro font-medium text-status-warning"
                    >
                      <Lock className="h-3 w-3" aria-hidden />
                      {t('map.visualRepair.touchesLocked')}
                    </span>
                  )}
                </div>
                {op.layer_ids && op.layer_ids.length > 0 && (
                  <div className="mt-0.5 text-micro text-ink-muted">
                    {op.layer_ids.join(', ')}
                  </div>
                )}
                {op.rationale && (
                  <div className="mt-0.5 line-clamp-2 text-micro text-ink-secondary">{op.rationale}</div>
                )}
              </li>
            ))}
          </ul>
          {typeof plan.base_revision === 'number' && (
            <p className="mb-2 text-micro text-ink-muted">
              {t('map.visualRepair.baseRevision', { revision: plan.base_revision })}
            </p>
          )}
          {phase.kind === 'applying' ? (
            <p className="flex items-center gap-2 text-caption text-ink-secondary" data-testid="visual-repair-applying">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t('map.visualRepair.applying')}
            </p>
          ) : (
            <div className="flex items-center gap-2">
              <button
                type="button"
                data-testid="visual-repair-approve"
                onClick={() => void approve(plan)}
                className="flex flex-1 items-center justify-center gap-1.5 rounded bg-status-accent px-2 py-1.5 text-caption font-semibold text-ink-on-accent hover:opacity-90"
              >
                <Check className="h-3.5 w-3.5" aria-hidden />
                {t('map.visualRepair.approve')}
              </button>
              <button
                type="button"
                data-testid="visual-repair-reject"
                title={t('map.visualRepair.rejectReasonHint')}
                onClick={() => void reject(plan)}
                className="flex items-center justify-center gap-1.5 rounded border border-edge-subtle px-2 py-1.5 text-caption text-ink hover:bg-surface-overlay"
              >
                <X className="h-3.5 w-3.5" aria-hidden />
                {t('map.visualRepair.reject')}
              </button>
            </div>
          )}
        </>
      )}

      {phase.kind === 'applied' && (
        <p
          data-testid="visual-repair-applied"
          className="flex items-center gap-2 text-caption text-status-success"
        >
          <Check className="h-4 w-4" aria-hidden />
          {phase.duplicate
            ? t('map.visualRepair.appliedDuplicate')
            : t('map.visualRepair.applied')}
        </p>
      )}
      {phase.kind === 'hard_stop' && (
        <p
          data-testid="visual-repair-hard-stop"
          className="flex items-start gap-2 text-caption text-status-warning"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {t('map.visualRepair.hardStop')}
        </p>
      )}
      {phase.kind === 'apply_failed' && (
        <p data-testid="visual-repair-apply-failed" className="text-caption text-status-critical">
          {t('map.visualRepair.applyFailed', { reason: phase.reason })}
        </p>
      )}
    </div>
  )
}
