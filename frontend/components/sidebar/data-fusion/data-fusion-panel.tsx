'use client';

/**
 * 多源实体融合工作台面板。
 *
 * 用户闭环：计划（新建/选择，CAS revision）→ 来源资格卡（ok/unresolvable
 * 双态如实展示）→ 预览（匹配统计徽章 + 有界样本 + warnings）→ 执行
 * （durable run 轮询 + 收据）→ 审阅队列（接受/拒绝，expected_revision CAS，
 * stale_review_revision 提示刷新）→ 已决定列表（显式解锁）→ 受权 commit
 * （commit_blocked 披露 outstanding）→ 结果分页表 + 质量摘要 → 增量刷新
 * （dry_run 影响面 → 确认后真实刷新）。
 * 诚实纪律：审阅项只展示匹配包携带的证据投影（不含名称字段）；长文本
 * 截断 ≤80 字符 + title 提示；全部分页有界（审阅/结果 ≤50/页，决定 ≤200/页，
 * 计划列表 ≤50）；typed 错误码与 correction_hint 原样披露。
 * 会话切换 epoch + sessionIdRef 双守卫（旧响应不污染新会话）。
 * 文案走 i18n（ADR-0144，namespace=dataFusion；zh-CN 为简体中文）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Database,
  GitMerge,
  ListChecks,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-react';

import {
  asDataFusionError,
  CommitPolicy,
  dataFusionApi,
  EntityKind,
  FusionDecisionListResponse,
  FusionDecisionView,
  FusionPlanInput,
  FusionPlanView,
  FusionPreviewResult,
  FusionRefreshDryRun,
  FusionResultPageView,
  FusionReviewQueueResponse,
  FusionRunView,
  FusionSourceCard,
  FusionSourceSpecInput,
} from '@/lib/api/data-fusion-authoring';
import { useDataFusionStore } from '@/lib/hooks/use-data-fusion';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';

interface DataFusionPanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

const ENTITY_KINDS: Array<{ value: EntityKind; labelKey: string }> = [
  { value: 'point_poi', labelKey: 'kindPointPoi' },
  { value: 'area_link', labelKey: 'kindAreaLink' },
  { value: 'line_link', labelKey: 'kindLineLink' },
];

const COMMIT_POLICIES: Array<{ value: CommitPolicy; labelKey: string }> = [
  { value: 'require_resolved', labelKey: 'policyRequireResolved' },
  { value: 'allow_unresolved', labelKey: 'policyAllowUnresolved' },
];

const PAIR_STATUS_STYLE: Record<string, string> = {
  matched: 'bg-status-success-soft text-status-success border-status-success-border',
  needs_review: 'bg-status-warning-soft text-status-warn border-status-warn-border',
  ambiguous: 'bg-status-warning-soft text-status-warn border-status-warn-border',
  unmatched: 'bg-status-neutral-soft text-ink-secondary border-status-neutral-border',
  rejected: 'bg-status-danger-soft text-status-danger border-status-danger-border',
};

const FIELD_CONFLICT_KEYS: Record<string, string> = {
  identical: 'conflictIdentical',
  conflict: 'conflictConflict',
  missing_a: 'conflictMissingA',
  missing_b: 'conflictMissingB',
  missing_both: 'conflictMissingBoth',
};

const REVIEW_PAGE_SIZE = 50;
const DECISIONS_PAGE_SIZE = 200;
const RESULT_PAGE_SIZE = 50;

/** 长文本截断显示（≤80 字符；完整值走 title 提示）。 */
function trunc(value: unknown, n = 80): string {
  const s = value == null ? '' : String(value);
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function statusBadgeClass(status: string): string {
  return PAIR_STATUS_STYLE[status]
    ?? 'bg-status-neutral-soft text-ink-secondary border-status-neutral-border';
}

interface Draft {
  fieldValues: string;
  note: string;
}

interface ProvisionalError {
  code: string;
  message: string;
  hint?: string;
  detail?: Record<string, unknown>;
}

export function DataFusionPanel({ sessionId, ownerToken }: DataFusionPanelProps): React.ReactElement | null {
  const t = useT('dataFusion');
  const open = useDataFusionStore((s) => s.open);
  const close = useDataFusionStore((s) => s.close);

  const containerRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: '[data-df-focus]' });

  // ── 计划作者态 ──
  const [plans, setPlans] = useState<FusionPlanView[]>([]);
  const [plansLoading, setPlansLoading] = useState(false);
  const [title, setTitle] = useState('');
  const [entityKind, setEntityKind] = useState<EntityKind>('point_poi');
  const [refA, setRefA] = useState('');
  const [nameFieldA, setNameFieldA] = useState('name');
  const [refB, setRefB] = useState('');
  const [nameFieldB, setNameFieldB] = useState('name');
  const [commitPolicy, setCommitPolicy] = useState<CommitPolicy>('require_resolved');

  // ── 计划 / 资格卡 / 预览 ──
  const [planId, setPlanId] = useState<string | null>(null);
  const [planRevision, setPlanRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [cards, setCards] = useState<FusionSourceCard[] | null>(null);
  const [describing, setDescribing] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<FusionPreviewResult | null>(null);

  // ── 执行 / 审阅 / 决定 ──
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<FusionRunView | null>(null);
  const [reviewPage, setReviewPage] = useState<FusionReviewQueueResponse | null>(null);
  const [reviewStart, setReviewStart] = useState(0);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewStale, setReviewStale] = useState(false);
  const [decidingKey, setDecidingKey] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [decisionsPage, setDecisionsPage] = useState<FusionDecisionListResponse | null>(null);
  const [decisionsLoading, setDecisionsLoading] = useState(false);
  const [unlockingKey, setUnlockingKey] = useState<string | null>(null);

  // ── commit / 结果 / 刷新 ──
  const [committing, setCommitting] = useState(false);
  const [commitBlocked, setCommitBlocked] = useState<{ outstanding: number | null } | null>(null);
  const [resultPage, setResultPage] = useState<FusionResultPageView | null>(null);
  const [resultStart, setResultStart] = useState(0);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const [refreshImpact, setRefreshImpact] = useState<FusionRefreshDryRun | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<ProvisionalError | null>(null);

  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId ?? null;
  const epochRef = useRef(0);

  const api = useMemo(
    () => (sessionId ? dataFusionApi.client(sessionId, ownerToken) : null),
    [sessionId, ownerToken],
  );

  const rulesReady = Boolean(refA.trim() && refB.trim());

  /** 配对状态中文徽章（组件内闭包 —— t 绑定可静态解析，键全部入 catalog）。 */
  const pairStatusLabel = useCallback((status: string): string => {
    switch (status) {
      case 'matched': return t('statMatched');
      case 'needs_review': return t('statNeedsReview');
      case 'ambiguous': return t('statAmbiguous');
      case 'unmatched': return t('statUnpaired');
      case 'rejected': return t('rejectAction');
      default: return status;
    }
  }, [t]);

  /** 字段冲突比较结果标签（词表 → catalog 键）。 */
  const conflictLabel = useCallback((comparison: string): string => {
    const key = FIELD_CONFLICT_KEYS[comparison];
    return key ? t(key) : comparison;
  }, [t]);

  const resetDownstream = useCallback(() => {
    setCards(null); setPreview(null); setRun(null); setResultPage(null);
    setReviewPage(null); setReviewStart(0); setReviewStale(false); setDrafts({});
    setDecisionsPage(null); setCommitBlocked(null); setRefreshImpact(null);
    setExpandedRows(new Set());
  }, []);

  /** 会话切换：旧会话的一切响应作废（先于 open 效果声明 —— 挂载时先自增
   * epoch，open 效果捕获的才是当前值；声明序 = 执行序）。 */
  useEffect(() => {
    epochRef.current += 1;
  }, [sessionId]);

  useEffect(() => {
    if (!open) {
      epochRef.current += 1;
      setPlans([]); setPlanId(null); setPlanRevision(0);
      setTitle(''); setEntityKind('point_poi');
      setRefA(''); setNameFieldA('name'); setRefB(''); setNameFieldB('name');
      setCommitPolicy('require_resolved');
      resetDownstream();
      setError(null);
      return;
    }
    if (!api) return;
    const epoch = epochRef.current;
    setPlansLoading(true);
    api.listPlans(50, 0)
      .then((res) => {
        if (epoch !== epochRef.current) return;
        setPlans(res.plans);
      })
      .catch((err) => {
        if (epoch !== epochRef.current) return;
        setError(asDataFusionError(err));
      })
      .finally(() => {
        if (epoch === epochRef.current) setPlansLoading(false);
      });
  }, [open, api, resetDownstream]);

  const describeInput = useCallback(() => ([
    {
      side: 'a' as const,
      kind: 'session_ref' as const,
      session_id: sessionId ?? undefined,
      ref: refA.trim(),
      entity_kind: entityKind,
      name_field: nameFieldA.trim() || 'name',
    },
    {
      side: 'b' as const,
      kind: 'session_ref' as const,
      session_id: sessionId ?? undefined,
      ref: refB.trim(),
      entity_kind: entityKind,
      name_field: nameFieldB.trim() || 'name',
    },
  ]), [sessionId, refA, refB, nameFieldA, nameFieldB, entityKind]);

  const describeSources = useCallback(async () => {
    if (!api) return;
    if (!refA.trim() || !refB.trim()) {
      setError({ code: 'invalid_input', message: t('needRefsError') });
      return;
    }
    const epoch = epochRef.current;
    setDescribing(true);
    try {
      const res = await api.describeSources(describeInput());
      if (epoch !== epochRef.current) return;
      setCards(res.sources);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setCards(null);
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setDescribing(false);
    }
  }, [api, describeInput, refA, refB, t]);

  /** 简化表单 → 计划 JSON（权重覆盖全部证据种类且 Σ=1；trust_priority 序）。 */
  const buildPlanInput = useCallback((): FusionPlanInput => {
    const src = (side: 'a' | 'b'): FusionSourceSpecInput => {
      const ref = (side === 'a' ? refA : refB).trim();
      const nameField = (side === 'a' ? nameFieldA : nameFieldB).trim() || 'name';
      return {
        kind: 'session_ref',
        session_id: sessionId ?? undefined,
        ref,
        label: side === 'a' ? 'A' : 'B',
        priority: side === 'a' ? 1 : 2,
        priority_reason: side === 'a'
          ? 'workbench: side A priority declaration'
          : 'workbench: side B secondary declaration',
        name_field: nameField,
        field_mappings: [{ source_field: nameField, output_field: 'name', rule: 'trust_priority' }],
      };
    };
    return {
      schema_version: 1,
      title: title.trim(),
      entity_kind: entityKind,
      source_a: src('a'),
      source_b: src('b'),
      // 与后端资格卡探测同一默认权重（name 主导，空间邻近为点实体核心证据之一）
      strategy: {
        weights: { external_id: 0, name: 0.4, address: 0.15, category: 0.15, distance: 0.3 },
        name_normalizers: ['trim', 'casefold', 'collapse_spaces'],
      },
      geometry: { adoption: 'primary_source' },
      commit_policy: commitPolicy,
      invalid_geometry_policy: 'exclude_count',
      output_name: title.trim() || 'data-fusion',
    };
  }, [title, entityKind, refA, refB, nameFieldA, nameFieldB, commitPolicy, sessionId]);

  const saveOrSyncPlan = useCallback(async (): Promise<{ planId: string; revision: number } | null> => {
    if (!api) return null;
    if (!refA.trim() || !refB.trim()) {
      setError({ code: 'invalid_input', message: t('needRefsError') });
      return null;
    }
    const plan = buildPlanInput();
    try {
      setError(null);
      if (planId) {
        const view = await api.updatePlan(planId, plan, planRevision);
        setPlanRevision(view.revision);
        return { planId: view.plan_id, revision: view.revision };
      }
      const view = await api.createPlan(plan);
      setPlanId(view.plan_id);
      setPlanRevision(view.revision);
      const epoch = epochRef.current;
      void api.listPlans(50, 0).then((res) => {
        if (epoch === epochRef.current) setPlans(res.plans);
      }).catch(() => { /* 列表刷新失败不打断 */ });
      return { planId: view.plan_id, revision: view.revision };
    } catch (err) {
      setError(asDataFusionError(err));
      return null;
    }
  }, [api, buildPlanInput, planId, planRevision, refA, refB, t]);

  const doSavePlan = useCallback(async () => {
    setSaving(true);
    try {
      const saved = await saveOrSyncPlan();
      if (!saved) return;
      await describeSources();
    } finally {
      setSaving(false);
    }
  }, [saveOrSyncPlan, describeSources]);

  const selectPlan = useCallback(async (pid: string) => {
    if (!api) return;
    if (!pid) {
      // 切回「新建计划」模式
      setPlanId(null); setPlanRevision(0);
      resetDownstream();
      return;
    }
    const epoch = epochRef.current;
    try {
      const view = await api.getPlan(pid);
      if (epoch !== epochRef.current) return;
      setTitle(view.plan.title ?? '');
      setEntityKind(view.plan.entity_kind);
      setRefA(view.plan.source_a.ref ?? '');
      setNameFieldA(view.plan.source_a.name_field || 'name');
      setRefB(view.plan.source_b.ref ?? '');
      setNameFieldB(view.plan.source_b.name_field || 'name');
      setCommitPolicy(view.plan.commit_policy ?? 'require_resolved');
      setPlanId(view.plan_id);
      setPlanRevision(view.revision);
      resetDownstream();
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    }
  }, [api, resetDownstream]);

  const doPreview = useCallback(async () => {
    const saved = await saveOrSyncPlan();
    if (!saved || !api) return;
    const epoch = epochRef.current;
    setPreviewing(true); setPreview(null);
    try {
      const res = await api.preview(saved.planId, saved.revision);
      if (epoch !== epochRef.current) return;
      setPreview(res);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setPreviewing(false);
    }
  }, [api, saveOrSyncPlan]);

  const loadReviewQueue = useCallback(async (runId: string, start: number) => {
    if (!api) return;
    const epoch = epochRef.current;
    setReviewLoading(true);
    setReviewStale(false);
    try {
      const page = await api.reviewQueue(runId, start, REVIEW_PAGE_SIZE);
      if (epoch !== epochRef.current) return;
      setReviewPage(page);
      setReviewStart(start);
      setDrafts({});
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setReviewLoading(false);
    }
  }, [api]);

  const loadDecisions = useCallback(async (pid: string) => {
    if (!api) return;
    const epoch = epochRef.current;
    setDecisionsLoading(true);
    try {
      const page = await api.listDecisions(pid, DECISIONS_PAGE_SIZE, 0);
      if (epoch !== epochRef.current) return;
      setDecisionsPage(page);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setDecisionsLoading(false);
    }
  }, [api]);

  const loadResultPage = useCallback(async (runId: string, start: number) => {
    if (!api) return;
    const epoch = epochRef.current;
    try {
      const page = await api.resultPage(runId, start, RESULT_PAGE_SIZE);
      if (epoch !== epochRef.current) return;
      setResultPage(page);
      setResultStart(start);
      setExpandedRows(new Set());
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    }
  }, [api]);

  const pollRun = useCallback(async (runId: string) => {
    if (!api) return;
    for (let i = 0; i < 240; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      if (sessionIdRef.current !== sessionId || !open) return;
      try {
        const view = await api.inspectRun(runId);
        if (sessionIdRef.current !== sessionId) return;
        setRun(view);
        if (view.status === 'completed') {
          void loadReviewQueue(runId, 0);
          if (planId) void loadDecisions(planId);
          return;
        }
        if (view.status === 'failed' || view.status === 'cancelled') return;
      } catch { /* 轮询失败继续 */ }
    }
  }, [api, sessionId, open, loadReviewQueue, loadDecisions, planId]);

  const doExecute = useCallback(async () => {
    const saved = await saveOrSyncPlan();
    if (!saved || !api) return;
    const epoch = epochRef.current;
    setRunning(true); setRun(null); setResultPage(null); setReviewPage(null); setCommitBlocked(null);
    try {
      const view = await api.execute(saved.planId, saved.revision, true);
      if (epoch !== epochRef.current) return;
      setRun(view);
      if (view.status === 'completed') {
        void loadReviewQueue(view.run_id, 0);
        void loadDecisions(saved.planId);
      } else if (view.status !== 'failed' && view.status !== 'cancelled') {
        void pollRun(view.run_id);
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setRunning(false);
    }
  }, [api, saveOrSyncPlan, pollRun, loadReviewQueue, loadDecisions]);

  const doCancel = useCallback(async () => {
    if (!api || !run) return;
    try {
      await api.cancelRun(run.run_id);
      useToastStore.getState().addToast(t('cancelRequestedToast'), 'info');
    } catch (err) {
      setError(asDataFusionError(err));
    }
  }, [api, run, t]);

  const setDraft = useCallback((pairKey: string, patch: Partial<Draft>) => {
    setDrafts((prev) => ({
      ...prev,
      [pairKey]: { ...(prev[pairKey] ?? { fieldValues: '', note: '' }), ...patch },
    }));
  }, []);

  /** 人工字段值输入解析：`字段=值`，逗号（中英文）分隔；空 → null。 */
  const parseFieldValues = useCallback((raw: string): Record<string, unknown> | null | 'invalid' => {
    const text = raw.trim();
    if (!text) return null;
    const out: Record<string, unknown> = {};
    for (const part of text.split(/[,，]/)) {
      const seg = part.trim();
      if (!seg) continue;
      const eq = seg.indexOf('=');
      if (eq <= 0) return 'invalid';
      out[seg.slice(0, eq).trim()] = seg.slice(eq + 1).trim();
    }
    return Object.keys(out).length ? out : null;
  }, []);

  const submitDecision = useCallback(async (item: {
    pair_key: string; idx_a: number; idx_b: number; decision?: FusionDecisionView | null;
  }, action: 'accept' | 'reject') => {
    if (!api || !planId || !run) return;
    const draft = drafts[item.pair_key] ?? { fieldValues: '', note: '' };
    let fieldValues: Record<string, unknown> | null = null;
    if (action === 'accept') {
      const parsed = parseFieldValues(draft.fieldValues);
      if (parsed === 'invalid') {
        setError({ code: 'invalid_input', message: t('invalidFieldValuesError') });
        return;
      }
      fieldValues = parsed;
    }
    const epoch = epochRef.current;
    setDecidingKey(item.pair_key);
    try {
      const view = await api.upsertDecision(planId, {
        pair_key: item.pair_key,
        action,
        decision_run_id: run.run_id,
        idx_a: item.idx_a,
        idx_b: item.idx_b,
        field_values: action === 'accept' ? fieldValues : undefined,
        note: draft.note.trim() || undefined,
        // CAS：既有决定带其 revision；新决定 expected_revision=0
        expected_revision: item.decision?.revision ?? 0,
      });
      if (epoch !== epochRef.current) return;
      setReviewPage((p) => (p
        ? { ...p, items: p.items.map((it) => (it.pair_key === item.pair_key ? { ...it, decision: view } : it)) }
        : p));
      void loadDecisions(planId);
      useToastStore.getState().addToast(
        action === 'accept' ? t('acceptedToast') : t('rejectedToast'),
        'success',
      );
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const e = asDataFusionError(err);
      if (e.code === 'stale_review_revision') setReviewStale(true);
      setError(e);
    } finally {
      if (epoch === epochRef.current) setDecidingKey(null);
    }
  }, [api, planId, run, drafts, parseFieldValues, loadDecisions, t]);

  const unlockDecision = useCallback(async (decision: FusionDecisionView) => {
    if (!api || !planId) return;
    const epoch = epochRef.current;
    setUnlockingKey(decision.pair_key);
    try {
      await api.deleteDecision(planId, decision.pair_key, decision.revision);
      if (epoch !== epochRef.current) return;
      setDecisionsPage((p) => (p
        ? { ...p, decisions: p.decisions.filter((d) => d.pair_key !== decision.pair_key) }
        : p));
      setReviewPage((p) => (p
        ? { ...p, items: p.items.map((it) => (it.pair_key === decision.pair_key ? { ...it, decision: null } : it)) }
        : p));
      useToastStore.getState().addToast(t('unlockedToast'), 'info');
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setUnlockingKey(null);
    }
  }, [api, planId, t]);

  const doCommit = useCallback(async () => {
    if (!api || !run) return;
    const epoch = epochRef.current;
    setCommitting(true);
    setCommitBlocked(null);
    try {
      const view = await api.commitRun(run.run_id);
      if (epoch !== epochRef.current) return;
      setRun(view);
      if (view.output_ref) {
        void loadResultPage(view.run_id, 0);
        useToastStore.getState().addToast(t('commitSuccessToast'), 'success');
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const e = asDataFusionError(err);
      if (e.code === 'commit_blocked') {
        const outstanding = e.detail?.outstanding;
        setCommitBlocked({ outstanding: typeof outstanding === 'number' ? outstanding : null });
      }
      setError(e);
    } finally {
      if (epoch === epochRef.current) setCommitting(false);
    }
  }, [api, run, loadResultPage, t]);

  const doRefreshDryRun = useCallback(async () => {
    if (!api || !planId) return;
    const epoch = epochRef.current;
    setRefreshing(true);
    setRefreshImpact(null);
    try {
      const res = await api.refresh(planId, planRevision, true);
      if (epoch !== epochRef.current) return;
      if (res.changed === false) {
        useToastStore.getState().addToast(res.message ?? t('refreshUnchangedToast'), 'info');
      } else if ('dry_run' in res && res.dry_run) {
        setRefreshImpact(res);
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setRefreshing(false);
    }
  }, [api, planId, planRevision, t]);

  const doRefreshApply = useCallback(async () => {
    if (!api || !planId || !refreshImpact) return;
    const epoch = epochRef.current;
    setRefreshing(true);
    try {
      const res = await api.refresh(planId, planRevision, false);
      if (epoch !== epochRef.current) return;
      if ('run' in res && res.run) {
        setRun(res.run);
        setPreview(null);
        setRefreshImpact(null);
        if (res.run.status === 'completed') {
          void loadReviewQueue(res.run.run_id, 0);
          void loadDecisions(planId);
        } else {
          void pollRun(res.run.run_id);
        }
        useToastStore.getState().addToast(t('refreshDoneToast'), 'success');
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataFusionError(err));
    } finally {
      if (epoch === epochRef.current) setRefreshing(false);
    }
  }, [api, planId, planRevision, refreshImpact, loadReviewQueue, loadDecisions, pollRun, t]);

  if (!open) return null;

  const renderCard = (card: FusionSourceCard) => {
    const isOk = card.state === 'ok';
    return (
      <div
        key={card.side}
        className="rounded-md border border-edge-subtle p-2.5"
        data-testid={`df-card-${card.side}`}
        data-state={card.state}
      >
        <div className="flex items-center justify-between pb-1.5">
          <span className="text-caption font-medium text-ink-muted">
            {t('sourceSide', { side: card.side.toUpperCase() })}
          </span>
          <span className={`text-caption ${isOk ? 'text-status-success' : 'text-status-error'}`}>
            {isOk ? t('stateOk') : t('stateUnresolvable')}
          </span>
        </div>
        {isOk ? (
          <div className="space-y-1 text-caption text-ink-muted">
            <div className="flex flex-wrap gap-x-3">
              <span>{t('rowCountLabel')} <span className="font-mono text-ink">{card.row_count ?? '—'}</span></span>
              <span>{t('crsLabel')} <span className="font-mono text-ink">{trunc(card.crs ?? t('crsUnknown'), 24)}</span>
                {card.crs_class ? `（${card.crs_class}）` : ''}</span>
              <span>{t('geometryLabel')} <span className="font-mono text-ink">{trunc(card.geometry ?? 'none', 24)}</span></span>
              {Boolean(card.invalid_geometry_excluded) && (
                <span className="text-status-warn">{t('invalidGeomExcluded', { count: card.invalid_geometry_excluded ?? 0 })}</span>
              )}
            </div>
            <div>
              {t('fieldsLabel')}
              {card.fields && Object.entries(card.fields).map(([key, f]) => (
                <span key={key} className={`pl-1.5 ${f.present ? 'text-ink' : 'text-status-error'}`}>
                  {key}={trunc(f.field, 24)}{f.present ? '✓' : '✗'}
                </span>
              ))}
              {!card.fields && <span className="pl-1">{t('fieldsUndeclared')}</span>}
            </div>
            <div>
              {t('temporalLabel')}
              {card.temporal_parse
                ? `${card.temporal_parse.parsed}/${card.temporal_parse.sample_n}`
                : t('temporalUndeclared')}
              {card.temporal_parse && (
                <span className="pl-1" title={card.temporal_parse.note}>({trunc(card.temporal_parse.note, 40)})</span>
              )}
            </div>
            <div>
              {t('licenseLabel')}
              <span className={card.license_declared ? 'text-ink' : 'text-status-warn'}>
                {card.license_declared ? trunc(card.license_declared, 40) : t('licenseUndeclared')}
              </span>
            </div>
            {card.content_fingerprint && (
              <div className="font-mono" title={card.content_fingerprint}>
                {t('fingerprintLabel', { value: trunc(card.content_fingerprint, 20) })}
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-1 text-caption" data-testid="df-card-unresolvable">
            <p className="text-status-error">
              <span className="font-mono">{card.error_code}</span> — {card.message}
            </p>
            {card.correction_hint && (
              <p className="text-ink-secondary" title={card.correction_hint}>{card.correction_hint}</p>
            )}
          </div>
        )}
      </div>
    );
  };

  const stats = preview?.match_stats;
  const receipt = run?.receipt ?? null;
  const commitReceipt = run?.commit_receipt ?? null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-ink/40 pt-[8vh]"
      data-testid="data-fusion-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('title')}
        data-testid="data-fusion-panel"
        className="flex max-h-[86vh] w-full max-w-[900px] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-edge-subtle px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-body-md font-semibold text-ink">
            <GitMerge size={15} aria-hidden /> {t('title')}
          </h2>
          <button
            type="button" data-df-focus onClick={close} aria-label={t('closeAria')}
            className="rounded-sm p-1 text-ink-secondary hover:bg-surface-hover hover:text-ink"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {/* 1 计划 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-plan">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-body-sm font-medium text-ink">
                <ListChecks size={14} aria-hidden /> {t('planSection')}
              </h3>
              {plansLoading && <RefreshCw size={13} className="animate-spin text-ink-muted" aria-hidden />}
            </div>
            <select
              value={planId ?? ''}
              onChange={(e) => void selectPlan(e.target.value)}
              aria-label={t('selectPlanAria')}
              data-testid="df-plan-select"
              className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
            >
              <option value="">{t('newPlanOption')}</option>
              {plans.map((p) => (
                <option key={p.plan_id} value={p.plan_id}>
                  {t('planOption', {
                    title: trunc(p.title || p.plan_id, 40),
                    status: p.status,
                    revision: p.revision,
                  })}
                </option>
              ))}
            </select>
            {!planId && plans.length === 0 && !plansLoading && (
              <p className="text-caption text-ink-muted">{t('noPlansHint')}</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('planTitlePlaceholder')}
                aria-label={t('planTitleLabel')}
                className="w-56 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
              />
              <select
                value={entityKind}
                onChange={(e) => { setEntityKind(e.target.value as EntityKind); setPreview(null); }}
                aria-label={t('entityKindLabel')}
                className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
              >
                {ENTITY_KINDS.map((k) => <option key={k.value} value={k.value}>{t(k.labelKey)}</option>)}
              </select>
              <select
                value={commitPolicy}
                onChange={(e) => setCommitPolicy(e.target.value as CommitPolicy)}
                aria-label={t('commitPolicyLabel')}
                className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
              >
                {COMMIT_POLICIES.map((p) => <option key={p.value} value={p.value}>{t(p.labelKey)}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md border border-edge-subtle p-2">
                <div className="pb-1 text-caption font-medium text-ink-muted">{t('sourceALabel')}</div>
                <input
                  value={refA}
                  onChange={(e) => { setRefA(e.target.value); setPreview(null); setCards(null); }}
                  placeholder={t('refPlaceholder')}
                  aria-label={t('refAAria')}
                  data-testid="df-ref-a"
                  className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm text-ink outline-none focus:border-status-accent"
                />
                <input
                  value={nameFieldA}
                  onChange={(e) => { setNameFieldA(e.target.value); setPreview(null); }}
                  placeholder={t('nameFieldPlaceholder')}
                  aria-label={t('nameFieldAAria')}
                  className="mt-1.5 w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                />
              </div>
              <div className="rounded-md border border-edge-subtle p-2">
                <div className="pb-1 text-caption font-medium text-ink-muted">{t('sourceBLabel')}</div>
                <input
                  value={refB}
                  onChange={(e) => { setRefB(e.target.value); setPreview(null); setCards(null); }}
                  placeholder={t('refPlaceholder')}
                  aria-label={t('refBAria')}
                  data-testid="df-ref-b"
                  className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm text-ink outline-none focus:border-status-accent"
                />
                <input
                  value={nameFieldB}
                  onChange={(e) => { setNameFieldB(e.target.value); setPreview(null); }}
                  placeholder={t('nameFieldPlaceholder')}
                  aria-label={t('nameFieldBAria')}
                  className="mt-1.5 w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                />
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                type="button" onClick={() => void doSavePlan()} disabled={saving || !rulesReady}
                data-testid="df-save-plan"
                className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCw size={14} className={saving || describing ? 'animate-spin' : ''} aria-hidden />
                {t('savePlanBtn')}
              </button>
              <button
                type="button" onClick={() => void describeSources()} disabled={describing || !rulesReady}
                className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Database size={14} aria-hidden /> {t('inspectSourcesBtn')}
              </button>
            </div>
            {!sessionId && <p className="text-caption text-status-error">{t('noSession')}</p>}
            {error && (
              <div className="rounded-md border border-status-error/40 bg-status-error/10 p-2 text-body-sm text-status-error" data-testid="df-error">
                <span className="font-mono text-caption">{error.code}</span> — {error.message}
                {error.hint && <p className="pt-0.5 text-caption" title={error.hint}>{error.hint}</p>}
              </div>
            )}
          </section>

          {/* 2 来源资格卡 */}
          <section className="space-y-2" data-testid="df-step-cards">
            <h3 className="text-body-sm font-medium text-ink">{t('cardsSection')}</h3>
            {describing && (
              <p className="flex items-center gap-1.5 text-caption text-ink-muted">
                <RefreshCw size={13} className="animate-spin" aria-hidden /> {t('inspectingText')}
              </p>
            )}
            {!describing && !cards && (
              <p className="text-caption text-ink-muted">{t('cardsHint')}</p>
            )}
            {cards && (
              <div className="grid grid-cols-2 gap-2">
                {cards.map(renderCard)}
              </div>
            )}
          </section>

          {/* 3 预览 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-preview">
            <div className="flex items-center justify-between">
              <h3 className="text-body-sm font-medium text-ink">{t('previewSection')}</h3>
              <button
                type="button" onClick={() => void doPreview()} disabled={previewing || !rulesReady}
                data-testid="df-preview"
                className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCw size={14} className={previewing ? 'animate-spin' : ''} aria-hidden /> {t('previewBtn')}
              </button>
            </div>
            {!preview && !previewing && (
              <p className="text-caption text-ink-muted">{t('previewHint')}</p>
            )}
            {preview && stats && (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  {preview.cached && (
                    <span className="rounded border border-status-neutral-border bg-status-neutral-soft px-1.5 py-0.5 text-caption text-ink-secondary">{t('cachedBadge')}</span>
                  )}
                  {preview.truncated_for_bound && (
                    <span className="text-caption text-status-warn">{t('truncatedNote')}</span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-1.5 text-caption sm:grid-cols-5" data-testid="df-match-badges">
                  {[
                    { label: t('statMatched'), value: stats.pairs_matched, cls: 'text-status-success' },
                    { label: t('statNeedsReview'), value: stats.pairs_needs_review, cls: 'text-status-warn' },
                    { label: t('statAmbiguous'), value: stats.pairs_ambiguous, cls: 'text-status-warn' },
                    { label: t('statHardBlocked'), value: stats.hard_blocked, cls: 'text-status-danger' },
                    { label: t('statUnpaired'), value: stats.unmatched_a + stats.unmatched_b, cls: 'text-ink-secondary',
                      note: t('unpairedSplit', { a: stats.unmatched_a, b: stats.unmatched_b }) },
                  ].map((b) => (
                    <div key={b.label} className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1">
                      <span className="text-ink-muted">{b.label}</span>
                      <span className={`pl-1 font-mono ${b.cls}`}>{b.value}</span>
                      {b.note && <div className="text-ink-muted">{b.note}</div>}
                    </div>
                  ))}
                </div>
                {preview.warnings.length > 0 && (
                  <div className="space-y-1" data-testid="df-warnings">
                    {preview.warnings.map((w, i) => (
                      <p key={`${w.item}-${i}`} className="text-caption text-status-warn" title={w.message}>
                        ⚠ {trunc(w.message, 80)}
                      </p>
                    ))}
                  </div>
                )}
                <div className="space-y-1" data-testid="df-samples">
                  {preview.samples.matched.slice(0, 5).map((s) => (
                    <p key={`m-${s.idx_a}-${s.idx_b}`} className="text-caption text-ink-muted">
                      {t('sampleMatchedLabel')}<span title={`${s.name_a} ≈ ${s.name_b}`}>{trunc(s.name_a, 40)} ≈ {trunc(s.name_b, 40)}</span>
                      <span className="font-mono">({s.score.toFixed(3)})</span>
                    </p>
                  ))}
                  {preview.samples.review.slice(0, 5).map((s) => (
                    <p key={`r-${s.idx_a}-${s.idx_b}`} className="text-caption text-ink-muted">
                      {t('sampleReviewLabel')}<span title={`${s.name_a} / ${s.name_b}`}>{trunc(s.name_a, 32)} / {trunc(s.name_b, 32)}</span>
                      <span className={`pl-1 ${s.status === 'ambiguous' ? 'text-status-warn' : ''}`}>{pairStatusLabel(s.status)}</span>
                      <span className="font-mono">({s.score != null ? s.score.toFixed(3) : '—'})</span>
                      {s.field_conflicts && Object.keys(s.field_conflicts).length > 0 && (
                        <span> {t('sampleConflicts', { fields: Object.keys(s.field_conflicts).join('、') })}</span>
                      )}
                    </p>
                  ))}
                  {preview.samples.matched.length === 0 && preview.samples.review.length === 0 && (
                    <p className="text-caption text-ink-muted">{t('samplesEmpty')}</p>
                  )}
                </div>
                <p className="text-caption text-ink-muted">
                  {t('budgetsNote', {
                    pairs: preview.budgets.max_candidate_pairs ?? '—',
                    features: preview.budgets.max_output_features ?? '—',
                  })}
                </p>
              </div>
            )}
          </section>

          {/* 4 执行 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-run">
            <div className="flex items-center justify-between">
              <h3 className="text-body-sm font-medium text-ink">{t('runSection')}</h3>
              <div className="flex gap-1.5">
                {run && (run.status === 'pending' || run.status === 'running') && (
                  <button
                    type="button" onClick={() => void doCancel()}
                    className="rounded-md border border-edge-subtle px-2 py-1 text-caption text-ink-secondary hover:bg-surface-hover"
                  >
                    {t('cancelBtn')}
                  </button>
                )}
                <button
                  type="button" onClick={() => void doExecute()} disabled={running || !rulesReady}
                  data-testid="df-execute"
                  className="inline-flex items-center gap-1.5 rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <PlayCircle size={14} aria-hidden /> {t('executeBtn')}
                </button>
              </div>
            </div>
            {!run && !running && (
              <p className="text-caption text-ink-muted">{t('runHint')}</p>
            )}
            {run && (
              <div className="space-y-1.5" data-testid="df-run">
                <p className="text-caption text-ink-muted">
                  {t('statusLabel')} <span className="font-mono text-ink">{run.status}</span>
                  {run.job_id != null && <span> · job #{run.job_id}</span>}
                  {run.idempotent_reuse && <span> · {t('idempotentReuse')}</span>}
                  {run.error_code && <span className="text-status-error"> · <span className="font-mono">{run.error_code}</span></span>}
                </p>
                {receipt && (
                  <div className="rounded border border-edge-subtle bg-surface-sunken p-2 text-caption text-ink-muted" data-testid="df-receipt">
                    <div>
                      {receipt.state === 'matched_pending_commit' ? t('receiptMatchedPending') : receipt.state}
                      {receipt.elapsed_s != null && <span> · {t('receiptElapsed', { seconds: receipt.elapsed_s })}</span>}
                    </div>
                    <div>
                      {t('receiptCounts', {
                        matched: receipt.match_stats.pairs_matched,
                        review: receipt.match_stats.pairs_needs_review + receipt.match_stats.pairs_ambiguous,
                        blocked: receipt.match_stats.hard_blocked,
                      })}
                    </div>
                    <div className="font-mono" title={`${receipt.fingerprint_a} / ${receipt.fingerprint_b}`}>
                      {t('fingerprintLabel', { value: trunc(receipt.fingerprint_a, 16) })} / {trunc(receipt.fingerprint_b, 16)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>

          {/* 5 审阅队列 */}
          {run && run.status === 'completed' && (
            <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-review">
              <div className="flex items-center justify-between">
                <h3 className="text-body-sm font-medium text-ink">
                  {t('reviewTitle', { total: reviewPage?.total ?? '…' })}
                </h3>
                <button
                  type="button" onClick={() => void loadReviewQueue(run.run_id, reviewStart)}
                  disabled={reviewLoading}
                  className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-2 py-1 text-caption text-ink-secondary hover:bg-surface-hover disabled:opacity-60"
                >
                  <RefreshCw size={13} className={reviewLoading ? 'animate-spin' : ''} aria-hidden /> {t('refreshQueueBtn')}
                </button>
              </div>
              {reviewStale && (
                <div className="rounded-md border border-status-warn-border bg-status-warning-soft p-2 text-body-sm text-status-warn" data-testid="df-review-stale">
                  {t('reviewStaleBanner')}
                </div>
              )}
              {reviewLoading && (
                <p className="flex items-center gap-1.5 text-caption text-ink-muted">
                  <RefreshCw size={13} className="animate-spin" aria-hidden /> {t('reviewLoadingText')}
                </p>
              )}
              {!reviewLoading && !reviewPage && (
                <p className="text-caption text-ink-muted">{t('reviewNotLoaded')}</p>
              )}
              {reviewPage && reviewPage.items.length === 0 && (
                <p className="text-caption text-ink-muted">{t('reviewNoItems')}</p>
              )}
              {reviewPage && reviewPage.items.map((item) => {
                const draft = drafts[item.pair_key] ?? { fieldValues: '', note: '' };
                const conflicts = Object.entries(item.field_conflicts ?? {});
                return (
                  <div
                    key={item.pair_key}
                    className="space-y-1.5 rounded-md border border-edge-subtle p-2"
                    data-testid="df-review-item"
                  >
                    <div className="flex flex-wrap items-center gap-1.5 text-caption">
                      <span className="font-mono text-ink" title={item.pair_key}>{trunc(item.pair_key, 24)}</span>
                      <span className="text-ink-muted">#{item.idx_a}×#{item.idx_b}</span>
                      <span className="font-mono text-ink">{item.score != null ? item.score.toFixed(3) : '—'}</span>
                      <span className={`rounded border px-1.5 py-0.5 ${statusBadgeClass(item.status)}`}>
                        {pairStatusLabel(item.status)}
                      </span>
                      {item.hard_block && (
                        <span className="rounded border border-status-danger-border bg-status-danger-soft px-1.5 py-0.5 text-status-danger">
                          {t('hardBlockBadge', { code: trunc(item.hard_block, 24) })}
                        </span>
                      )}
                      {item.id_pinned && (
                        <span className="rounded border border-status-info-border bg-status-info-soft px-1.5 py-0.5 text-ink-secondary">{t('idPinnedBadge')}</span>
                      )}
                      {item.decision && (
                        <span className={`rounded border px-1.5 py-0.5 ${item.decision.action === 'accept' ? 'border-status-success-border bg-status-success-soft text-status-success' : 'border-status-danger-border bg-status-danger-soft text-status-danger'}`}>
                          {t('decidedBadge', {
                            action: item.decision.action === 'accept' ? t('acceptAction') : t('rejectAction'),
                            revision: item.decision.revision,
                          })}
                        </span>
                      )}
                    </div>
                    {conflicts.length > 0 && (
                      <p className="text-caption text-ink-muted">
                        {t('fieldConflictsLabel')}
                        {conflicts.map(([field, cmp]) => (
                          <span key={field} className={`pl-1.5 ${cmp === 'conflict' ? 'text-status-warn' : ''}`}>
                            {field}={conflictLabel(cmp)}
                          </span>
                        ))}
                      </p>
                    )}
                    {item.evidence && item.evidence.length > 0 && (
                      <p className="text-caption text-ink-muted">
                        {t('evidenceLabel')}
                        {item.evidence.map((e, i) => (
                          <span key={`${e.kind}-${i}`} className="pl-1.5" title={e.detail ? trunc(JSON.stringify(e.detail), 80) : undefined}>
                            {e.kind}={e.value != null ? trunc(e.value, 16) : t('evidenceMissing')}
                          </span>
                        ))}
                      </p>
                    )}
                    {item.geometry_evidence && Object.keys(item.geometry_evidence).length > 0 && (
                      <p className="text-caption text-ink-muted" title={trunc(JSON.stringify(item.geometry_evidence), 200)}>
                        {t('geometryEvidenceLabel')}{trunc(JSON.stringify(item.geometry_evidence), 80)}
                      </p>
                    )}
                    <div className="flex flex-wrap items-center gap-1.5">
                      <input
                        value={draft.fieldValues}
                        onChange={(e) => setDraft(item.pair_key, { fieldValues: e.target.value })}
                        placeholder={t('fieldValuePlaceholder')}
                        aria-label={t('fieldValueAria', { pair: trunc(item.pair_key, 16) })}
                        className="w-72 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption"
                      />
                      <input
                        value={draft.note}
                        onChange={(e) => setDraft(item.pair_key, { note: e.target.value })}
                        placeholder={t('notePlaceholder')}
                        aria-label={t('noteAria', { pair: trunc(item.pair_key, 16) })}
                        className="w-44 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption"
                      />
                      <button
                        type="button" onClick={() => void submitDecision(item, 'accept')}
                        disabled={decidingKey === item.pair_key}
                        data-testid="df-accept"
                        className="rounded-md border border-status-success-border px-2 py-1 text-caption text-status-success hover:bg-status-success-soft disabled:opacity-60"
                      >
                        {t('acceptBtn')}
                      </button>
                      <button
                        type="button" onClick={() => void submitDecision(item, 'reject')}
                        disabled={decidingKey === item.pair_key}
                        data-testid="df-reject"
                        className="rounded-md border border-status-danger-border px-2 py-1 text-caption text-status-danger hover:bg-status-danger-soft disabled:opacity-60"
                      >
                        {t('rejectBtn')}
                      </button>
                    </div>
                  </div>
                );
              })}
              {reviewPage && reviewPage.total > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-caption text-ink-muted">
                    {t('pageItems', {
                      from: reviewStart + 1,
                      to: reviewStart + reviewPage.items.length,
                      total: reviewPage.total,
                    })}
                  </span>
                  <div className="flex gap-1.5">
                    <button
                      type="button" disabled={reviewStart === 0 || reviewLoading}
                      onClick={() => void loadReviewQueue(run.run_id, Math.max(0, reviewStart - REVIEW_PAGE_SIZE))}
                      className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                    >{t('prevPage')}</button>
                    <button
                      type="button" disabled={!reviewPage.has_more || reviewLoading}
                      onClick={() => void loadReviewQueue(run.run_id, reviewPage.cursor)}
                      className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                    >{t('nextPage')}</button>
                  </div>
                </div>
              )}
            </section>
          )}

          {/* 6 已决定列表 */}
          {planId && (
            <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-decisions">
              <div className="flex items-center justify-between">
                <h3 className="text-body-sm font-medium text-ink">{t('decisionsSection')}</h3>
                <button
                  type="button" onClick={() => void loadDecisions(planId)}
                  disabled={decisionsLoading}
                  className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-2 py-1 text-caption text-ink-secondary hover:bg-surface-hover disabled:opacity-60"
                >
                  <RefreshCw size={13} className={decisionsLoading ? 'animate-spin' : ''} aria-hidden /> {t('loadDecisionsBtn')}
                </button>
              </div>
              {decisionsLoading && <p className="text-caption text-ink-muted">{t('decisionsLoadingText')}</p>}
              {!decisionsLoading && !decisionsPage && (
                <p className="text-caption text-ink-muted">{t('decisionsHint')}</p>
              )}
              {decisionsPage && decisionsPage.decisions.length === 0 && (
                <p className="text-caption text-ink-muted">{t('decisionsEmpty')}</p>
              )}
              {decisionsPage && decisionsPage.decisions.map((d) => (
                <div key={d.pair_key} className="flex flex-wrap items-center gap-1.5 text-caption">
                  <span className="font-mono text-ink" title={d.pair_key}>{trunc(d.pair_key, 24)}</span>
                  <span className={`rounded border px-1.5 py-0.5 ${d.action === 'accept' ? 'border-status-success-border bg-status-success-soft text-status-success' : 'border-status-danger-border bg-status-danger-soft text-status-danger'}`}>
                    {d.action === 'accept' ? t('acceptAction') : t('rejectAction')}
                  </span>
                  {d.note && <span className="text-ink-muted" title={d.note}>{trunc(d.note, 40)}</span>}
                  <span className="text-ink-muted">
                    rev{d.revision}{d.locked ? ` · ${t('lockedMark')}` : ''}
                  </span>
                  <button
                    type="button" onClick={() => void unlockDecision(d)}
                    disabled={unlockingKey === d.pair_key}
                    data-testid="df-unlock"
                    className="rounded border border-edge-subtle px-1.5 py-0.5 text-ink-secondary hover:bg-surface-hover disabled:opacity-60"
                  >
                    {t('unlockBtn')}
                  </button>
                </div>
              ))}
            </section>
          )}

          {/* 7 提交 + 结果 */}
          {run && run.status === 'completed' && (
            <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-commit">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-1.5 text-body-sm font-medium text-ink">
                  <ShieldCheck size={14} aria-hidden /> {t('commitSection')}
                </h3>
                <button
                  type="button" onClick={() => void doCommit()}
                  disabled={committing || Boolean(run.output_ref)}
                  data-testid="df-commit"
                  className="inline-flex items-center gap-1.5 rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {committing && <RefreshCw size={14} className="animate-spin" aria-hidden />}
                  {run.output_ref ? t('committedBtn') : t('commitBtn')}
                </button>
              </div>
              {commitBlocked && (
                <div className="rounded-md border border-status-error/40 bg-status-error/10 p-2 text-body-sm text-status-error" data-testid="df-commit-blocked">
                  <p className="font-medium">
                    {t('commitBlockedTitle', { count: commitBlocked.outstanding ?? '—' })}
                  </p>
                  <p className="pt-0.5 text-caption">{t('commitBlockedHint')}</p>
                </div>
              )}
              {run.output_ref && (
                <p className="text-caption text-ink-muted">
                  {t('outputRefLabel')}<span className="font-mono" title={run.output_ref}>{trunc(run.output_ref, 48)}</span>
                </p>
              )}
              {commitReceipt && (
                <div className="rounded border border-edge-subtle bg-surface-sunken p-2 text-caption text-ink-muted" data-testid="df-quality">
                  <div>
                    {t('qualityCounts', {
                      merged: commitReceipt.counts.merged,
                      withheld: commitReceipt.counts.withheld_review,
                      rejected: commitReceipt.counts.rejected,
                      singleA: commitReceipt.counts.single_a,
                      singleB: commitReceipt.counts.single_b,
                    })}
                  </div>
                  <div>
                    {t('qualityTotals', {
                      total: commitReceipt.total_features,
                      unresolved: commitReceipt.unresolved_field_count,
                      decided: commitReceipt.decided_pairs,
                      orphan: commitReceipt.orphan_decisions,
                    })}
                  </div>
                </div>
              )}
              {run.output_ref && !resultPage && (
                <p className="text-caption text-ink-muted">{t('resultsLoading', { size: RESULT_PAGE_SIZE })}</p>
              )}
              {resultPage && (
                <>
                  <div className="max-h-56 overflow-auto rounded border border-edge-subtle" data-testid="df-results">
                    <table className="w-full text-caption">
                      <thead className="sticky top-0 bg-surface-sunken text-ink-muted">
                        <tr>
                          <th className="px-2 py-1 text-left font-medium">{t('colName')}</th>
                          <th className="px-2 py-1 text-left font-medium">{t('colAddress')}</th>
                          <th className="px-2 py-1 text-left font-medium">{t('colStatus')}</th>
                          <th className="px-2 py-1 text-left font-medium">{t('colOrigin')}</th>
                          <th className="px-2 py-1 text-left font-medium">{t('colProvenance')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resultPage.features.map((f, i) => {
                          const props = f.properties ?? {};
                          const fusion = (props._fusion ?? null) as
                            | { origin?: string; match?: { status?: string } }
                            | null;
                          const provText = fusion ? JSON.stringify(fusion) : '';
                          return (
                            <tr key={i} className="border-t border-edge-subtle/60">
                              <td className="px-2 py-1 text-ink" title={props.name != null ? String(props.name) : undefined}>
                                {trunc(props.name, 40)}
                              </td>
                              <td className="px-2 py-1 text-ink" title={props.address != null ? String(props.address) : undefined}>
                                {trunc(props.address, 40)}
                              </td>
                              <td className="px-2 py-1 text-ink-muted">{fusion?.match?.status ?? ''}</td>
                              <td className="px-2 py-1 text-ink-muted">{fusion?.origin ?? ''}</td>
                              <td className="px-2 py-1">
                                {expandedRows.has(i) ? (
                                  <div className="max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-sunken p-1 font-mono text-ink-muted">
                                    {provText || '—'}
                                    <button
                                      type="button" onClick={() => setExpandedRows((s) => { const n = new Set(s); n.delete(i); return n; })}
                                      className="ml-1 underline"
                                    >{t('collapseBtn')}</button>
                                  </div>
                                ) : (
                                  <>
                                    <span className="text-ink-muted" title={provText}>{trunc(provText, 80)}</span>
                                    <button
                                      type="button" onClick={() => setExpandedRows((s) => { const n = new Set(s); n.add(i); return n; })}
                                      className="ml-1 text-ink-secondary underline"
                                    >{t('expandBtn')}</button>
                                  </>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-caption text-ink-muted">
                      {t('pageRows', {
                        from: resultStart + 1,
                        to: resultStart + resultPage.features.length,
                        total: resultPage.total,
                      })}
                    </span>
                    <div className="flex gap-1.5">
                      <button
                        type="button" disabled={resultStart === 0}
                        onClick={() => void loadResultPage(run.run_id, Math.max(0, resultStart - RESULT_PAGE_SIZE))}
                        className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                      >{t('prevPage')}</button>
                      <button
                        type="button" disabled={!resultPage.has_more}
                        onClick={() => void loadResultPage(run.run_id, resultPage.cursor)}
                        className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                      >{t('nextPage')}</button>
                    </div>
                  </div>
                </>
              )}
            </section>
          )}

          {/* 8 增量刷新 */}
          {planId && (
            <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="df-step-refresh">
              <div className="flex items-center justify-between">
                <h3 className="text-body-sm font-medium text-ink">{t('refreshSection')}</h3>
                <button
                  type="button" onClick={() => void doRefreshDryRun()}
                  disabled={refreshing}
                  data-testid="df-refresh-dry"
                  className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} aria-hidden /> {t('refreshCheckBtn')}
                </button>
              </div>
              <p className="text-caption text-ink-muted">{t('refreshHint')}</p>
              {refreshImpact && (
                <div className="space-y-1 rounded border border-edge-subtle bg-surface-sunken p-2 text-caption text-ink-muted" data-testid="df-refresh-impact">
                  <div>
                    {t('impactSummary', {
                      addedA: refreshImpact.side_a.added.length,
                      addedB: refreshImpact.side_b.added.length,
                      removedA: refreshImpact.side_a.removed.length,
                      removedB: refreshImpact.side_b.removed.length,
                      changed: refreshImpact.total_new,
                    })}
                  </div>
                  <div>
                    {t('impactRatio', {
                      ratio: `${(refreshImpact.affected_ratio * 100).toFixed(1)}%`,
                      cap: `${(refreshImpact.cap * 100).toFixed(0)}%`,
                      verdict: refreshImpact.verdict,
                    })}
                  </div>
                  <div>
                    {t('impactDecisions', {
                      inherited: refreshImpact.decisions_inherited,
                      stale: refreshImpact.decisions_stale_total,
                      orphan: refreshImpact.decisions_orphan,
                    })}
                  </div>
                  {refreshImpact.safe ? (
                    <button
                      type="button" onClick={() => void doRefreshApply()} disabled={refreshing}
                      data-testid="df-refresh-apply"
                      className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
                    >
                      {refreshing && <RefreshCw size={14} className="animate-spin" aria-hidden />}
                      {t('refreshApplyBtn')}
                    </button>
                  ) : (
                    <p className="text-status-warn">{t('refreshUnsafeNote')}</p>
                  )}
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
