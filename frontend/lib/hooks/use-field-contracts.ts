'use client';

/**
 * G44 业务字段合同工作台 hook —— 面板状态机 + REST 编排。
 *
 * 诚实纪律：
 * - 激活闸门三条件同时满足才放开：评估 completed 且 0 违规 且
 *   verified.rules_digest === 当前草稿 normalized_digest（评估后改规则 →
 *   digest 不匹配 → 强制重评，绝不放宽）；
 * - findings 分页 cursor 只前进/后退显式记录（next_cursor 透传，不猜偏移）；
 * - 批量填值复用 G24 selection handle 引擎（ids/bbox scope），绝不自造
 *   第二套选择：总数未知 / 读取不完整 / >200 一律拒绝，不自动拆多笔；
 * - typed 错误码（revision_conflict / assessment_stale / activation_race…）
 *   原样上抛给 UI 收敛，不解析文案判断语义。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { create } from 'zustand';

import {
  activateContract,
  asFieldContractError,
  bindingStatus,
  createContract,
  findingsPage,
  getAssessment,
  listContracts,
  observeFields,
  reviseContract,
  retireContract,
  startAssessment,
  type FieldContractErrorView,
} from '@/lib/api/field-contracts';
import type {
  FieldAssessmentView,
  FieldBindingStatusView,
  FieldContractDocument,
  FieldContractView,
  FieldFindingsPageView,
  FieldObserveView,
} from '@/lib/types/field-contracts';
import {
  createSelectionHandle,
  fetchSelectionFeatures,
  materializeSelectionHandle,
  releaseSelectionHandle,
} from '@/lib/api/selection-handles';
import { decideFeatureEditPlan, previewFeatureEdit } from '@/lib/api/feature-edit';
import { getSelection } from '@/lib/selection/selection-store';
import { featureIdFromProperties } from '@/lib/edit/layer-edit-bridge';

/** 客户端上限（与后端合同词表一致；可见计数器用）。 */
export const MAX_DRAFT_FIELDS = 64;
export const MAX_CROSS_RULES = 64;
export const MAX_ENUM_VALUES = 256;
/** 批量填值目标上限：超过即阻断（引导缩小选择，绝不自动拆多笔提交）。 */
export const MAX_BATCH_TARGETS = 200;
const ASSESS_POLL_INTERVAL_MS = 1500;
const ASSESS_MAX_POLLS = 120;
const FINDINGS_PAGE_LIMIT = 20;

/* ── 面板开关 store（与 useDataJoinStore 同款单例模式） ── */

interface FieldContractsPanelStore {
  open: boolean;
  /** 预选数据集 ref（从数据源页跳入时可带）。 */
  presetRef: string | null;
  openWith: (ref?: string | null) => void;
  close: () => void;
}

export const useFieldContractsPanelStore = create<FieldContractsPanelStore>((set) => ({
  open: false,
  presetRef: null,
  openWith: (ref) => set({ open: true, presetRef: ref ?? null }),
  close: () => set({ open: false, presetRef: null }),
}));

/* ── 工作台 hook ── */

export interface BatchFillResult {
  targets: number;
  changed: number;
  violations: number;
  unmatched: number;
}

export interface UseFieldContractsOptions {
  sessionId?: string | null;
  ownerToken?: string | null;
  refId?: string | null;
}

export interface UseFieldContractsResult {
  /* 概览 */
  binding: FieldBindingStatusView | null;
  observe: FieldObserveView | null;
  contracts: FieldContractView[];
  /** status==='draft' 的合同（规则编辑器的保存目标）。 */
  draft: FieldContractView | null;
  /** status==='active' 的合同（退役 / 绑定展示）。 */
  active: FieldContractView | null;
  /** 历史（superseded / retired）。 */
  history: FieldContractView[];
  overviewLoading: boolean;
  refresh: () => Promise<void>;
  /* 草稿保存 */
  saving: boolean;
  saveDraft: (document: FieldContractDocument) => Promise<FieldContractView | null>;
  /* 评估 */
  assessment: FieldAssessmentView | null;
  assessing: boolean;
  runAssessment: (contractId: string) => Promise<void>;
  /* findings 分页 */
  findings: FieldFindingsPageView | null;
  findingsLoading: boolean;
  codeFilter: string;
  setCodeFilter: (code: string) => void;
  loadFindingsPage: (cursor?: string | null) => Promise<void>;
  nextFindingsPage: () => Promise<void>;
  prevFindingsPage: () => Promise<void>;
  hasPrevFindingsPage: boolean;
  /* 激活 / 退役 */
  canActivate: boolean;
  /** 激活被闸门挡住时的 i18n 键（null = 可激活）。 */
  activateGateKey: string | null;
  activateGateParams: Record<string, string | number> | null;
  activating: boolean;
  activate: () => Promise<FieldContractView | null>;
  retiring: boolean;
  retire: () => Promise<FieldContractView | null>;
  /* 批量填值 */
  batchRunning: boolean;
  batchResult: BatchFillResult | null;
  runBatchFill: (field: string, valueLiteral: string) => Promise<BatchFillResult | null>;
  /* 错误面 */
  error: FieldContractErrorView | null;
  clearError: () => void;
}

export function useFieldContracts({
  sessionId,
  ownerToken,
  refId,
}: UseFieldContractsOptions): UseFieldContractsResult {
  const [binding, setBinding] = useState<FieldBindingStatusView | null>(null);
  const [observe, setObserve] = useState<FieldObserveView | null>(null);
  const [contracts, setContracts] = useState<FieldContractView[]>([]);
  const [overviewLoading, setOverviewLoading] = useState(false);

  const [saving, setSaving] = useState(false);
  const [assessment, setAssessment] = useState<FieldAssessmentView | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [findings, setFindings] = useState<FieldFindingsPageView | null>(null);
  const [findingsLoading, setFindingsLoading] = useState(false);
  const [codeFilter, setCodeFilter] = useState('');
  /** cursor 游历栈（栈顶 = 当前页 cursor；null = 首页）。 */
  const cursorStackRef = useRef<(string | null)[]>([null]);
  const [cursorDepth, setCursorDepth] = useState(1);
  /** 在飞 findings 请求序号：只有最新一次请求可写回（防旧响应覆盖新页）。 */
  const findingsReqRef = useRef(0);
  const [activating, setActivating] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchResult, setBatchResult] = useState<BatchFillResult | null>(null);
  const [error, setError] = useState<FieldContractErrorView | null>(null);
  /** 评估轮询停止标记（关闭面板 / 新评估启动时置位）。 */
  const pollStopRef = useRef(0);
  const codeFilterRef = useRef(codeFilter);
  codeFilterRef.current = codeFilter;

  const draft = useMemo(
    () => contracts.find((c) => c.status === 'draft' && c.ref_id === refId) ?? null,
    [contracts, refId],
  );
  const active = useMemo(
    () => contracts.find((c) => c.status === 'active' && c.ref_id === refId) ?? null,
    [contracts, refId],
  );
  const history = useMemo(
    () => contracts.filter(
      (c) => c.ref_id === refId && (c.status === 'superseded' || c.status === 'retired'),
    ),
    [contracts, refId],
  );

  /** 概览三拉：binding + observe + 合同列表（草稿/生效/历史都来自列表）。 */
  const refresh = useCallback(async () => {
    if (!sessionId || !refId) return;
    setOverviewLoading(true);
    try {
      const [b, o, l] = await Promise.all([
        bindingStatus(sessionId, refId, ownerToken),
        observeFields(sessionId, refId, ownerToken),
        listContracts(sessionId, refId, ownerToken),
      ]);
      setBinding(b);
      setObserve(o);
      setContracts(l.contracts ?? []);
      setError(null);
    } catch (err) {
      setError(asFieldContractError(err));
    } finally {
      setOverviewLoading(false);
    }
  }, [sessionId, refId, ownerToken]);

  useEffect(() => {
    if (!sessionId || !refId) return;
    setAssessment(null);
    setFindings(null);
    cursorStackRef.current = [null];
    setCursorDepth(1);
    setCodeFilter('');
    void refresh();
  }, [sessionId, refId, refresh]);

  /** 草稿保存：无草稿 → POST；有草稿 → PUT（revision CAS）。 */
  const saveDraft = useCallback(async (document: FieldContractDocument): Promise<FieldContractView | null> => {
    if (!sessionId || !refId) return null;
    setSaving(true);
    setError(null);
    try {
      const view = draft
        ? await reviseContract(sessionId, draft.contract_id, draft.revision, document, ownerToken)
        : await createContract(sessionId, refId, document, ownerToken);
      // 保存后刷新列表（draft_exists 等服务端状态的本地收敛）
      const l = await listContracts(sessionId, refId, ownerToken);
      setContracts(l.contracts ?? []);
      return view;
    } catch (err) {
      setError(asFieldContractError(err));
      return null;
    } finally {
      setSaving(false);
    }
  }, [sessionId, refId, draft, ownerToken]);

  /** findings 分页读取（cursor 透传；code 过滤变化时回首页）。 */
  const loadFindingsPage = useCallback(async (cursor?: string | null, assessmentId?: string) => {
    const id = assessmentId ?? assessment?.id;
    if (!sessionId || !id) return;
    const req = ++findingsReqRef.current;
    setFindingsLoading(true);
    try {
      const page = await findingsPage(
        sessionId,
        id,
        { cursor: cursor ?? null, limit: FINDINGS_PAGE_LIMIT, code: codeFilterRef.current || null },
        ownerToken,
      );
      if (req !== findingsReqRef.current) return;
      setFindings(page);
    } catch (err) {
      if (req !== findingsReqRef.current) return;
      setError(asFieldContractError(err));
    } finally {
      if (req === findingsReqRef.current) setFindingsLoading(false);
    }
  }, [sessionId, assessment, ownerToken]);

  /** 评估流：start →（queued/running 时）每 1.5s 轮询 ≤120 次 → findings 首页。 */
  const runAssessment = useCallback(async (contractId: string) => {
    if (!sessionId) return;
    pollStopRef.current += 1; // 掐断上一轮轮询
    const myPoll = pollStopRef.current;
    setAssessing(true);
    setError(null);
    try {
      const start = await startAssessment(sessionId, contractId, ownerToken);
      let view = start.assessment;
      setAssessment(view);
      let polls = 0;
      while (
        view &&
        (view.status === 'queued' || view.status === 'running') &&
        polls < ASSESS_MAX_POLLS &&
        pollStopRef.current === myPoll
      ) {
        await new Promise((r) => setTimeout(r, ASSESS_POLL_INTERVAL_MS));
        if (pollStopRef.current !== myPoll) return;
        polls += 1;
        view = await getAssessment(sessionId, view.id, ownerToken);
        setAssessment(view);
      }
      if (pollStopRef.current !== myPoll) return;
      if (view && view.status === 'completed') {
        cursorStackRef.current = [null];
        setCursorDepth(1);
        await loadFindingsPage(null, view.id);
      }
    } catch (err) {
      setError(asFieldContractError(err));
    } finally {
      if (pollStopRef.current === myPoll) setAssessing(false);
    }
  }, [sessionId, ownerToken, loadFindingsPage]);

  const setCodeFilterWithReset = useCallback((code: string) => {
    setCodeFilter(code);
    codeFilterRef.current = code;
    cursorStackRef.current = [null];
    setCursorDepth(1);
    setFindings(null);
  }, []);

  const nextFindingsPage = useCallback(async () => {
    const cursor = findings?.next_cursor;
    if (!cursor) return;
    cursorStackRef.current.push(cursor);
    setCursorDepth(cursorStackRef.current.length);
    await loadFindingsPage(cursor);
  }, [findings, loadFindingsPage]);

  const prevFindingsPage = useCallback(async () => {
    if (cursorStackRef.current.length <= 1) return;
    cursorStackRef.current.pop();
    const cursor = cursorStackRef.current[cursorStackRef.current.length - 1];
    setCursorDepth(cursorStackRef.current.length);
    await loadFindingsPage(cursor);
  }, [loadFindingsPage]);

  /* 激活闸门：completed + 0 违规 + rules_digest 与当前草稿一致。 */
  const canActivate = useMemo(() => {
    if (!draft || !assessment) return false;
    if (assessment.status !== 'completed') return false;
    if ((assessment.counts?.violations_total ?? 0) > 0) return false;
    const verified = assessment.verified?.rules_digest ?? null;
    return Boolean(verified) && verified === draft.normalized_digest;
  }, [draft, assessment]);

  const activateGate = useMemo((): { key: string | null; params: Record<string, string | number> | null } => {
    if (canActivate) return { key: null, params: null };
    if (!draft) return { key: 'activate.gateNoAssessment', params: null };
    if (!assessment || assessment.status !== 'completed') return { key: 'activate.gateNoAssessment', params: null };
    const violations = assessment.counts?.violations_total ?? 0;
    if (violations > 0) return { key: 'activate.gateViolations', params: { count: violations } };
    return { key: 'activate.gateDigestMismatch', params: null };
  }, [canActivate, draft, assessment]);

  const activate = useCallback(async (): Promise<FieldContractView | null> => {
    if (!sessionId || !draft) return null;
    setActivating(true);
    setError(null);
    try {
      const res = await activateContract(sessionId, draft.contract_id, ownerToken);
      await refresh();
      return res.contract;
    } catch (err) {
      setError(asFieldContractError(err));
      return null;
    } finally {
      setActivating(false);
    }
  }, [sessionId, draft, ownerToken, refresh]);

  const retire = useCallback(async (): Promise<FieldContractView | null> => {
    if (!sessionId || !active) return null;
    setRetiring(true);
    setError(null);
    try {
      const res = await retireContract(sessionId, active.contract_id, ownerToken);
      await refresh();
      return res.contract;
    } catch (err) {
      setError(asFieldContractError(err));
      return null;
    } finally {
      setRetiring(false);
    }
  }, [sessionId, active, ownerToken, refresh]);

  /**
   * 批量填值（G24 selection handle 复用，不自造选择引擎）：
   * 1. 读当前 selection（ids 优先；bbox 谓词走完整集合，其余谓词拒绝）；
   * 2. handle materialize 拿权威总数；翻页收全 ids（≤200）；
   * 3. 总数未知 / 收取不全 / >200 → 拒绝（不自动拆多笔提交）；
   * 4. preview → 0 违规才 approve；violations/unmatched 如实上报。
   */
  const runBatchFill = useCallback(async (
    field: string,
    valueLiteral: string,
  ): Promise<BatchFillResult | null> => {
    if (!sessionId || !refId) return null;
    let value: unknown;
    try {
      value = JSON.parse(valueLiteral);
    } catch {
      setError({ code: 'invalid_value', message: valueLiteral });
      return null;
    }
    const sel = getSelection();
    if (!sel || !sel.selected_ids?.length) {
      setError({ code: 'no_selection', message: 'empty selection' });
      return null;
    }
    if (sel.artifact_ref && sel.artifact_ref !== refId) {
      setError({ code: 'selection_ref_mismatch', message: sel.artifact_ref });
      return null;
    }
    // 选择全集寻址（G24）：selection store 已带服务端 handle（框选/条件选择
    // 超 ids 上限时的权威全集）→ 直接复用；否则 bbox 谓词建 handle；再否则
    // ids scope（≤50 恒完整）。'in' 谓词且 ids 被截断且无 handle → 拒绝
    // （宁缺毋假，不自造第二套选择语义）。
    const truncatedByIds = !sel.handle
      && sel.predicate?.kind === 'in'
      && sel.selected_ids.length < (sel.matched_count ?? sel.selected_ids.length);
    if (truncatedByIds) {
      setError({ code: 'selection_scope_unavailable', message: sel.predicate?.kind ?? '' });
      return null;
    }
    const scope = sel.predicate?.kind === 'bbox' && !sel.handle
      ? { kind: 'bbox' as const, bbox: sel.predicate.bbox }
      : { kind: 'ids' as const, ids: sel.selected_ids };
    setBatchRunning(true);
    setError(null);
    /** 仅释放自建 handle（selection store 的 handle 归地图所有）。 */
    let ownedHandle: string | null = null;
    try {
      let handle: string;
      if (sel.handle) {
        handle = sel.handle;
      } else {
        const meta = await createSelectionHandle({ sessionId, refId, scope, ownerToken });
        handle = meta.handle;
        ownedHandle = meta.handle;
      }
      const mat = await materializeSelectionHandle({ sessionId, refId, handle, ownerToken });
      const total = mat.feature_count;
      if (typeof total !== 'number' || !Number.isFinite(total)) {
        setError({ code: 'selection_count_unknown', message: String(total) });
        return null;
      }
      if (total > MAX_BATCH_TARGETS) {
        setError({ code: 'selection_too_large', message: String(total) });
        return null;
      }
      // 翻页收全 ids（总量已 ≤200；truncated/短读即拒绝）
      const ids: string[] = [];
      let cursor: string | null = null;
      for (let guard = 0; guard < 20; guard++) {
        const page = await fetchSelectionFeatures({
          sessionId, refId, handle, limit: MAX_BATCH_TARGETS, cursor, ownerToken,
        });
        for (const f of page.features) {
          const id = featureIdFromProperties(
            (f.properties ?? {}) as Record<string, unknown> | null,
            f.id as string | number | undefined,
          );
          if (id && !ids.includes(id)) ids.push(id);
        }
        if (!page.nextCursor) break;
        cursor = page.nextCursor;
      }
      if (ids.length > MAX_BATCH_TARGETS) {
        setError({ code: 'selection_too_large', message: String(ids.length) });
        return null;
      }
      if (ids.length < total) {
        setError({ code: 'selection_incomplete', message: `${ids.length}/${total}` });
        return null;
      }
      // preview → （0 违规才）approve：同一服务端事务链路
      const preview = await previewFeatureEdit(
        sessionId,
        { refId, featureIds: ids },
        [{ op: 'set_fields', fields: { [field]: value } }],
        ownerToken,
      );
      const violations = preview.validation?.length ?? 0;
      const targets = preview.n_targets ?? 0;
      const result: BatchFillResult = {
        targets,
        changed: 0,
        violations,
        unmatched: ids.length - targets,
      };
      if (violations > 0 || !preview.committable) {
        setBatchResult(result);
        return result;
      }
      if (preview.plan_id) {
        const decision = await decideFeatureEditPlan(sessionId, preview.plan_id, 'approve', ownerToken);
        result.changed = decision.receipt?.changed_wfids?.length ?? 0;
      }
      setBatchResult(result);
      return result;
    } catch (err) {
      setError(asFieldContractError(err));
      return null;
    } finally {
      if (ownedHandle) {
        void releaseSelectionHandle({ sessionId, refId, handle: ownedHandle, ownerToken });
      }
      setBatchRunning(false);
    }
  }, [sessionId, refId, ownerToken]);

  const clearError = useCallback(() => setError(null), []);

  // 卸载 / 数据集切换：停轮询
  useEffect(() => () => { pollStopRef.current += 1; }, []);

  return {
    binding,
    observe,
    contracts,
    draft,
    active,
    history,
    overviewLoading,
    refresh,
    saving,
    saveDraft,
    assessment,
    assessing,
    runAssessment,
    findings,
    findingsLoading,
    codeFilter,
    setCodeFilter: setCodeFilterWithReset,
    loadFindingsPage,
    nextFindingsPage,
    prevFindingsPage,
    hasPrevFindingsPage: cursorDepth > 1,
    canActivate,
    activateGateKey: activateGate.key,
    activateGateParams: activateGate.params,
    activating,
    activate,
    retiring,
    retire,
    batchRunning,
    batchResult,
    runBatchFill,
    error,
    clearError,
  };
}
