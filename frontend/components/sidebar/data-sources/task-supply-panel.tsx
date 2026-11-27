'use client';

/**
 * 任务数据供给面板（GOAL-20261126-02 WP4）。
 *
 * 用户旅程：填写任务范围 → 评估覆盖（矩阵 + 缺口/披露）→ 生成供给计划
 * （替代方案显式列出）→ 选择替代方案 → 批准执行（产物 ref 回执）。
 * 只读覆盖预览不写当前制图地图；采用/执行走既有 revision/确认通道。
 */

import { useCallback, useEffect, useState } from 'react';

import {
  compareTaskCoverage,
  createSupplyPlan,
  executeSupplyPlan,
  type CoverageEvaluation,
  type SupplyPlan,
} from '@/lib/api/task-data-supply';
import { describeApiError } from '@/lib/api/transport';
import { useT } from '@/lib/i18n/useT';

const STATE_KEYS: Record<string, string> = {
  draft: 'taskSupply.stateDraft',
  requires_confirmation: 'taskSupply.stateNeedsConfirmation',
  ready: 'taskSupply.stateReady',
  executing: 'taskSupply.stateExecuting',
  completed: 'taskSupply.stateCompleted',
  partial: 'taskSupply.statePartial',
  cancelled: 'taskSupply.stateCancelled',
};

const ACTION_KEYS: Record<string, string> = {
  use_bound: 'taskSupply.actionUseBound',
  acquire: 'taskSupply.actionAcquire',
  derive: 'taskSupply.actionDerive',
  clarify: 'taskSupply.actionClarify',
  user_upload: 'taskSupply.actionUserUpload',
};

const VERDICT_KEYS: Record<string, string> = {
  full: 'taskSupply.verdictFull',
  partial: 'taskSupply.verdictPartial',
  declared_only: 'taskSupply.verdictDeclaredOnly',
  none: 'taskSupply.verdictNone',
  unknown: 'taskSupply.verdictUnknown',
};

function verdictLabel(verdict: string, t: (k: string) => string): string {
  return t(VERDICT_KEYS[verdict] ?? 'taskSupply.verdictUnknown');
}

interface TaskSupplyPanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

export function TaskSupplyPanel({ sessionId, ownerToken }: TaskSupplyPanelProps) {
  const t = useT('taskSupply');
  const [place, setPlace] = useState('');
  const [bbox, setBbox] = useState('');
  const [timeRange, setTimeRange] = useState('');
  const [roles, setRoles] = useState('subject');
  const [budget, setBudget] = useState('0');
  const [coverageRoles, setCoverageRoles] = useState<
    Array<{ role: string; evaluations: CoverageEvaluation[]; unknown_reasons: string[] }>
  >([]);
  const [plan, setPlan] = useState<SupplyPlan | null>(null);
  const [busy, setBusy] = useState<'coverage' | 'plan' | 'execute' | null>(null);
  const [error, setError] = useState('');

  // P3-7C（R1 评审 C）：会话切换时重置计划与覆盖视图，防旧会话残留
  useEffect(() => {
    setPlan(null);
    setCoverageRoles([]);
    setError('');
  }, [sessionId]);

  const _scope = useCallback(() => {
    const bboxList = bbox.trim()
      ? bbox.split(',').map((v) => Number(v.trim())).filter((v) => Number.isFinite(v))
      : null;
    const time = timeRange.includes(',')
      ? timeRange.split(',').map((v) => v.trim())
      : null;
    return {
      session_id: sessionId ?? '',
      place: place.trim(),
      bbox: bboxList && bboxList.length === 4 ? bboxList : null,
      time_range: time && time.length === 2 ? time : null,
      roles: roles.split(',').map((r) => r.trim()).filter(Boolean),
    };
  }, [bbox, place, roles, sessionId, timeRange]);

  const runCoverage = useCallback(async () => {
    if (!sessionId) return;
    setError('');
    setBusy('coverage');
    try {
      const out = await compareTaskCoverage(_scope(), { ownerToken });
      setCoverageRoles(out.coverage?.roles ?? []);
    } catch (err) {
      setError(`${t('errorPrefix')}: ${describeApiError(err, t('errorPrefix'))}`);
    } finally {
      setBusy(null);
    }
  }, [_scope, ownerToken, sessionId, t]);

  const runPlan = useCallback(async () => {
    if (!sessionId) return;
    setError('');
    setBusy('plan');
    try {
      const budgetNum = Number(budget.trim() || '0');
      const out = await createSupplyPlan(
        { ..._scope(), budget_max_bytes: Number.isFinite(budgetNum) && budgetNum > 0 ? budgetNum : null },
        { ownerToken },
      );
      setPlan(out.plan);
    } catch (err) {
      setError(`${t('errorPrefix')}: ${describeApiError(err, t('errorPrefix'))}`);
    } finally {
      setBusy(null);
    }
  }, [_scope, budget, ownerToken, sessionId, t]);

  const chooseAlternative = useCallback(
    (role: string, kind: string) => {
      // P2-4C（R1 评审 C）：函数式读最新 state 收集全部已决策——
      // 多角色快速连点不丢先前选择；失败回滚乐观状态
      let prevPlan: SupplyPlan | null = null;
      const nextChoices: Record<string, string> = {};
      setPlan((prev) => {
        prevPlan = prev;
        for (const s of prev?.steps ?? []) {
          if (s.user_choice) nextChoices[s.role] = s.user_choice;
        }
        nextChoices[role] = kind;
        return {
          ...(prev ?? plan)!,
          steps: (prev?.steps ?? []).map((s) =>
            s.role === role ? { ...s, user_choice: kind } : s,
          ),
        };
      });
      setError('');
      setBusy('plan');
      createSupplyPlan(
        {
          ..._scope(),
          budget_max_bytes: prevPlan?.budget_max_bytes ?? null,
          choices: nextChoices,
        },
        { ownerToken },
      )
        .then((out) => setPlan(out.plan))
        .catch((err) => {
          // 失败回滚：还原该角色的乐观决策（不静默残留假状态）
          setPlan((cur) => cur ? {
            ...cur,
            steps: cur.steps.map((s) =>
              s.role === role ? { ...s, user_choice: '' } : s),
          } : cur);
          setError(`${t('errorPrefix')}: ${describeApiError(err, t('errorPrefix'))}`);
        })
        .finally(() => setBusy(null));
    },
    [_scope, ownerToken, plan, t],
  );

  const runExecute = useCallback(async () => {
    if (!sessionId || !plan) return;
    if (plan.state === 'requires_confirmation') {
      // 选择即重建已覆盖该路径；防御性提示（不静默执行需确认的计划）
      setError(t('executeBlocked'));
      return;
    }
    setError('');
    setBusy('execute');
    try {
      const out = await executeSupplyPlan(plan.plan_id, sessionId, true, [], { ownerToken });
      setPlan(out.plan);
    } catch (err) {
      setError(`${t('errorPrefix')}: ${describeApiError(err, t('errorPrefix'))}`);
    } finally {
      setBusy(null);
    }
  }, [ownerToken, plan, sessionId, t]);

  const hasScope = Boolean(place.trim() || bbox.trim());

  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto p-2.5" data-testid="task-supply-panel">
      <p className="text-caption text-ink-secondary">{t('hint')}</p>
      <div className="flex flex-col gap-1.5">
        <label className="text-caption text-ink-secondary" htmlFor="tds-place">{t('place')}</label>
        <input
          id="tds-place"
          data-testid="tds-place"
          value={place}
          onChange={(e) => setPlace(e.target.value)}
          placeholder={t('placePlaceholder')}
          className="h-control-md w-full rounded-md border border-edge-subtle bg-surface px-2 text-body"
        />
        <label className="text-caption text-ink-secondary" htmlFor="tds-bbox">{t('bbox')}</label>
        <input
          id="tds-bbox"
          data-testid="tds-bbox"
          value={bbox}
          onChange={(e) => setBbox(e.target.value)}
          placeholder={t('bboxPlaceholder')}
          className="h-control-md w-full rounded-md border border-edge-subtle bg-surface px-2 text-body"
        />
        <label className="text-caption text-ink-secondary" htmlFor="tds-time">{t('timeRange')}</label>
        <input
          id="tds-time"
          data-testid="tds-time"
          value={timeRange}
          onChange={(e) => setTimeRange(e.target.value)}
          placeholder={t('timePlaceholder')}
          className="h-control-md w-full rounded-md border border-edge-subtle bg-surface px-2 text-body"
        />
        <label className="text-caption text-ink-secondary" htmlFor="tds-roles">{t('roles')}</label>
        <input
          id="tds-roles"
          data-testid="tds-roles"
          value={roles}
          onChange={(e) => setRoles(e.target.value)}
          placeholder={t('rolesPlaceholder')}
          className="h-control-md w-full rounded-md border border-edge-subtle bg-surface px-2 text-body"
        />
        <label className="text-caption text-ink-secondary" htmlFor="tds-budget">{t('budget')}</label>
        <input
          id="tds-budget"
          data-testid="tds-budget"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          inputMode="numeric"
          className="h-control-md w-full rounded-md border border-edge-subtle bg-surface px-2 text-body"
        />
      </div>
      <div className="flex gap-1.5">
        <button
          type="button"
          data-testid="tds-run-coverage"
          disabled={!hasScope || busy !== null || !sessionId}
          onClick={runCoverage}
          className="h-control-md flex-1 rounded-md border border-edge-subtle bg-surface text-body hover:bg-surface-hover disabled:opacity-50"
        >
          {busy === 'coverage' ? t('running') : t('runCoverage')}
        </button>
        <button
          type="button"
          data-testid="tds-create-plan"
          disabled={!hasScope || busy !== null || !sessionId}
          onClick={runPlan}
          className="h-control-md flex-1 rounded-md border border-edge-subtle bg-surface text-body hover:bg-surface-hover disabled:opacity-50"
        >
          {busy === 'plan' ? t('creating') : t('createPlan')}
        </button>
      </div>
      {error ? (
        <p className="text-caption text-status-critical" data-testid="tds-error" role="alert">{error}</p>
      ) : null}

      {coverageRoles.length > 0 ? (
        <section aria-label={t('coverageTitle')} className="flex flex-col gap-1.5">
          <h3 className="text-caption font-medium text-ink">{t('coverageTitle')}</h3>
          {coverageRoles.map((rc) => (
            <div key={rc.role} className="rounded-md border border-edge-subtle p-1.5">
              <p className="text-caption font-medium text-ink">{rc.role}</p>
              {rc.evaluations.length === 0 ? (
                <ul className="text-caption text-ink-secondary">
                  {(rc.unknown_reasons ?? []).map((u, i) => (
                    <li key={i}>{u}</li>
                  ))}
                </ul>
              ) : (
                rc.evaluations.map((ev) => (
                  <div key={ev.candidate_id} className="mt-1 flex flex-col gap-0.5 text-caption" data-testid="tds-eval-row">
                    <span className="text-ink">
                      {ev.candidate_id} — {verdictLabel(ev.verdict, t)}
                    </span>
                    {(ev.gaps ?? []).map((g, i) => (
                      <span key={i} className="text-ink-secondary">
                        {g.dimension}: {g.detail}
                      </span>
                    ))}
                    {(ev.disclosures ?? []).map((d, i) => (
                      <span key={i} className="text-ink-secondary">{d}</span>
                    ))}
                  </div>
                ))
              )}
            </div>
          ))}
        </section>
      ) : null}

      {plan ? (
        <section aria-label={t('planTitle')} className="flex flex-col gap-1.5">
          <h3 className="text-caption font-medium text-ink">{t('planTitle')}</h3>
          <p className="text-caption text-ink-secondary" data-testid="tds-plan-state">
            {t('planState')}: {t(STATE_KEYS[plan.state] ?? '') || plan.state}
            {plan.budget_status === 'exceeded' ? ` — ${t('budgetExceeded')}` : ''}
          </p>
          {plan.steps.map((s) => (
            <div key={s.role} className="rounded-md border border-edge-subtle p-1.5" data-testid="tds-step">
              <p className="text-caption font-medium text-ink">
                {s.role} — {t(ACTION_KEYS[s.action] ?? '') || s.action}
                {s.candidate_id ? ` (${s.candidate_id})` : ''}
              </p>
              {(s.disclosures ?? []).map((d, i) => (
                <p key={i} className="text-caption text-ink-secondary">{d}</p>
              ))}
              {s.outcome && s.outcome !== 'pending' ? (
                <p className="text-caption text-ink-secondary">
                  {t('stepOutcome')}: {s.outcome}
                  {s.receipt_ref ? ` · ${t('receipt')}: ${s.receipt_ref}` : ''}
                </p>
              ) : null}
              {(s.alternatives ?? []).length > 0 ? (
                <div className="mt-1 flex flex-col gap-1">
                  <span className="text-caption text-ink-secondary">{t('alternatives')}</span>
                  {s.alternatives.map((a) => (
                    <label key={a.kind} className="flex items-center gap-1 text-caption">
                      <input
                        type="radio"
                        name={`tds-choice-${s.role}`}
                        checked={s.user_choice === a.kind}
                        onChange={() => chooseAlternative(s.role, a.kind)}
                      />
                      <span>{a.kind}: {a.note}</span>
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
          <button
            type="button"
            data-testid="tds-execute"
            disabled={busy !== null || plan.state !== 'ready'}
            onClick={runExecute}
            className="h-control-md rounded-md border border-edge-subtle bg-surface text-body hover:bg-surface-hover disabled:opacity-50"
          >
            {busy === 'execute' ? t('executing') : t('approveExecute')}
          </button>
        </section>
      ) : null}
    </div>
  );
}
