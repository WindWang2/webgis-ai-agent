'use client';

/**
 * 制图规范与系列作者工作台（WP4）。
 *
 * 单面板多 tab（规范 / 系列 / 传播），专业简洁 —— 避免每图重复大面板。
 * 权威面 = 后端 REST（/api/v1/design-specs 已实现；/api/v1/map-series 为
 * WP3 并行契约）。面板内部状态不进全局 store（避免 cross-tab stale 扩散）：
 * 每次 tab 激活 forceRefresh 重新拉列表，mutation 后 client 显式 invalidateCache。
 *
 * 「在地图打开」仅展示 session_id 引用信息，不做跨会话加载（系列只存引用
 * 与计划，不建第二地图状态 —— WP3 纪律）。传播 = 逐图 CAS：apply 前强制
 * 一致性检查通过（error 级 findings 禁用 + 提示），逐成员 receipt 如实披露。
 */
import { useEffect, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import {
  asDesignSpecError,
  createDesignSpec,
  createDesignSpecVersion,
  getDesignSpec,
  getDesignSpecPreview,
  listDesignSpecs,
  publishDesignSpec,
  retireDesignSpec,
} from '@/lib/api/design-specs';
import {
  applyPropagation,
  getMapSeriesConsistency,
  getMapSeries,
  listMapSeries,
  previewPropagation,
  removeMapSeriesMember,
  upsertMapSeriesMember,
} from '@/lib/api/map-series';
import type {
  ConsistencyResult,
  DesignSpecDetail,
  DesignSpecPayload,
  DesignSpecPreview,
  DesignSpecSummary,
  DesignSpecViolation,
  MapSeriesDetail,
  MapSeriesMember,
  MapSeriesSummary,
  PropagationPreviewResult,
  PropagationReceipt,
  SeriesMemberRole,
} from '@/lib/design-series/contracts';

type Props = { onClose?: () => void };
type TabKey = 'specs' | 'series' | 'propagation';

const MEMBER_ROLES: SeriesMemberRole[] = ['primary', 'comparison', 'context', 'detail'];

function digestShort(digest: string): string {
  const tail = digest.includes(':') ? (digest.split(':').pop() ?? '') : digest;
  return tail.slice(0, 8);
}

function splitList(input: string): string[] {
  return input.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
}

/** 新建表单 → design_spec.v1 payload 骨架（其余分节由后端补默认）。 */
function skeletonPayload(input: {
  name: string; description: string; themeId: string; palette: string;
  method: string; sharedRequired: boolean; targets: string;
}): DesignSpecPayload {
  return {
    spec_version: 'design_spec.v1',
    name: input.name.trim(),
    description: input.description.trim() || null,
    color: {
      theme_id: input.themeId.trim() || 'cartographic.light_interactive',
      palette_sequential: input.palette.trim() || null,
    },
    classification: {
      default_method: input.method.trim() || 'quantiles',
      shared_required: input.sharedRequired,
    },
    output: { targets: splitList(input.targets).length > 0 ? splitList(input.targets) : ['interactive'] },
  };
}

function ViolationList({ items }: { items: DesignSpecViolation[] }) {
  return (
    <ul className="list-none space-y-0.5">
      {items.map((v, i) => (
        <li key={`${v.reason}-${i}`} className="text-meta">
          <span className="font-medium">{v.reason}</span>
          {v.field ? <span className="text-ink-muted"> · {v.field}</span> : null}
          {v.message ? <span className="text-ink-muted"> — {v.message}</span> : null}
        </li>
      ))}
    </ul>
  );
}

interface MemberFormState {
  open: boolean;
  editing: string | null;
  memberId: string;
  title: string;
  sessionId: string;
  role: SeriesMemberRole;
  tags: string;
  locked: string;
  enabled: boolean;
}

const EMPTY_MEMBER_FORM: MemberFormState = {
  open: false, editing: null, memberId: '', title: '', sessionId: '',
  role: 'comparison', tags: '', locked: '', enabled: true,
};

export function DesignSeriesPanel({ onClose }: Props) {
  const t = useT('designSeries');
  const [tab, setTab] = useState<TabKey>('specs');

  // ── 规范 tab ──
  const [specs, setSpecs] = useState<DesignSpecSummary[] | null>(null);
  const [specsLoading, setSpecsLoading] = useState(false);
  const [specsError, setSpecsError] = useState<string | null>(null);
  const [selectedSpecId, setSelectedSpecId] = useState<string | null>(null);
  const [specDetail, setSpecDetail] = useState<DesignSpecDetail | null>(null);
  const [specDetailLoading, setSpecDetailLoading] = useState(false);
  const [preview, setPreview] = useState<DesignSpecPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    name: '', description: '', themeId: 'cartographic.light_interactive',
    palette: 'Blues', method: 'quantiles', sharedRequired: true,
    targets: 'interactive, png, pdf, svg',
  });
  const [retireNote, setRetireNote] = useState('');

  // ── 系列 tab ──
  const [series, setSeries] = useState<MapSeriesSummary[] | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [selectedSeriesId, setSelectedSeriesId] = useState<string | null>(null);
  const [seriesDetail, setSeriesDetail] = useState<MapSeriesDetail | null>(null);
  const [seriesDetailLoading, setSeriesDetailLoading] = useState(false);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [memberForm, setMemberForm] = useState<MemberFormState>(EMPTY_MEMBER_FORM);
  const [consistency, setConsistency] = useState<ConsistencyResult | null>(null);
  const [consistencyLoading, setConsistencyLoading] = useState(false);
  const [consistencySeriesId, setConsistencySeriesId] = useState<string | null>(null);

  // ── 传播 tab ──
  const [propSpecId, setPropSpecId] = useState<string>('');
  const [propVersion, setPropVersion] = useState<string>('latest');
  const [propSeriesId, setPropSeriesId] = useState<string>('');
  const [propPreview, setPropPreview] = useState<PropagationPreviewResult | null>(null);
  const [propPreviewLoading, setPropPreviewLoading] = useState(false);
  const [receipts, setReceipts] = useState<PropagationReceipt[] | null>(null);
  const [applyArmed, setApplyArmed] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [createViolations, setCreateViolations] = useState<DesignSpecViolation[]>([]);

  // 静态键 helper（闭包内字面量键 —— i18n 扫描器可静态判定）。
  const specStatusLabel = (status: string) =>
    status === 'published' ? t('specs.statusPublished')
      : status === 'retired' ? t('specs.statusRetired') : t('specs.statusDraft');
  const roleLabel = (role: SeriesMemberRole) =>
    role === 'primary' ? t('series.rolePrimary')
      : role === 'comparison' ? t('series.roleComparison')
        : role === 'context' ? t('series.roleContext') : t('series.roleDetail');

  const loadSpecs = async (force = false) => {
    setSpecsLoading(true);
    setSpecsError(null);
    try {
      const out = await listDesignSpecs({ forceRefresh: force });
      setSpecs(out.items);
    } catch (err) {
      setSpecsError(asDesignSpecError(err).code);
    } finally {
      setSpecsLoading(false);
    }
  };

  const loadSeries = async (force = false) => {
    setSeriesLoading(true);
    setSeriesError(null);
    try {
      const out = await listMapSeries({ forceRefresh: force });
      setSeries(out.items);
    } catch (err) {
      setSeriesError(asDesignSpecError(err).code);
    } finally {
      setSeriesLoading(false);
    }
  };

  useEffect(() => {
    void loadSpecs();
    // 只在挂载时加载一次；后续由 tab 激活 / 刷新按钮显式触发。
  }, []);

  // Esc 关抽屉：成员表单 / 新建表单 → 逐层关；否则关整个工作台（弹层）。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (memberForm.open) { setMemberForm(EMPTY_MEMBER_FORM); return; }
      if (showCreate) { setShowCreate(false); return; }
      onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [memberForm.open, showCreate, onClose]);

  const switchTab = (next: TabKey) => {
    setTab(next);
    if (next === 'specs') void loadSpecs(true);
    else if (next === 'series') void loadSeries(true);
    else {
      void loadSpecs(true);
      void loadSeries(true);
      if (!propSpecId && selectedSpecId) setPropSpecId(selectedSpecId);
      if (!propSeriesId && selectedSeriesId) setPropSeriesId(selectedSeriesId);
    }
  };

  const selectSpec = async (id: string) => {
    setSelectedSpecId(id);
    setSpecDetail(null);
    setPreview(null);
    setNotice(null);
    setSpecDetailLoading(true);
    try {
      setSpecDetail(await getDesignSpec(id));
    } catch (err) {
      setNotice({ ok: false, text: asDesignSpecError(err).message });
    } finally {
      setSpecDetailLoading(false);
    }
  };

  const runSpecPreview = async () => {
    if (!selectedSpecId) return;
    setPreviewLoading(true);
    try {
      setPreview(await getDesignSpecPreview(selectedSpecId, 'latest'));
    } catch (err) {
      setNotice({ ok: false, text: asDesignSpecError(err).message });
    } finally {
      setPreviewLoading(false);
    }
  };

  const submitCreate = async () => {
    if (!form.name.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      await createDesignSpec(skeletonPayload(form));
      setShowCreate(false);
      setNotice({ ok: true, text: t('specs.versionDone') });
      await loadSpecs(true);
    } catch (err) {
      const e = asDesignSpecError(err);
      const label = e.code === 'DESIGN_SPEC_NAME_CONFLICT' ? t('specs.nameConflict')
        : e.code === 'DESIGN_SPEC_INVALID' ? t('specs.invalidPayload')
          : t('specs.requestFailed');
      setNotice({ ok: false, text: `${label}: ${e.message}` });
      setCreateViolations(e.violations);
    } finally {
      setBusy(false);
    }
  };

  const runVersion = async () => {
    if (!selectedSpecId || !specDetail) return;
    setBusy(true);
    setNotice(null);
    try {
      await createDesignSpecVersion(selectedSpecId, specDetail.payload);
      setNotice({ ok: true, text: t('specs.versionDone') });
      await loadSpecs(true);
    } catch (err) {
      setNotice({ ok: false, text: `${t('specs.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setBusy(false);
    }
  };

  const runPublish = async () => {
    if (!selectedSpecId) return;
    setBusy(true);
    setNotice(null);
    try {
      await publishDesignSpec(selectedSpecId);
      setNotice({ ok: true, text: t('specs.publishDone') });
      await loadSpecs(true);
      if (specDetail) await selectSpec(selectedSpecId);
    } catch (err) {
      setNotice({ ok: false, text: `${t('specs.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setBusy(false);
    }
  };

  const runRetire = async () => {
    if (!selectedSpecId) return;
    setBusy(true);
    setNotice(null);
    try {
      await retireDesignSpec(selectedSpecId, retireNote.trim());
      setNotice({ ok: true, text: t('specs.retireDone') });
      setRetireNote('');
      await loadSpecs(true);
      if (specDetail) await selectSpec(selectedSpecId);
    } catch (err) {
      setNotice({ ok: false, text: `${t('specs.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setBusy(false);
    }
  };

  const selectSeries = async (id: string) => {
    setSelectedSeriesId(id);
    setSelectedMemberId(null);
    setSeriesDetail(null);
    setNotice(null);
    setSeriesDetailLoading(true);
    try {
      setSeriesDetail(await getMapSeries(id));
    } catch (err) {
      setNotice({ ok: false, text: asDesignSpecError(err).message });
    } finally {
      setSeriesDetailLoading(false);
    }
  };

  const runConsistency = async (seriesId: string) => {
    if (!seriesId) return;
    setConsistencyLoading(true);
    setConsistency(null);
    setNotice(null);
    try {
      const out = await getMapSeriesConsistency(seriesId);
      setConsistency(out);
      setConsistencySeriesId(seriesId);
    } catch (err) {
      setNotice({ ok: false, text: `${t('series.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setConsistencyLoading(false);
    }
  };

  const saveMember = async () => {
    const seriesId = selectedSeriesId;
    if (!seriesId || !memberForm.open || !memberForm.memberId.trim()) return;
    setBusy(true);
    setNotice(null);
    const member: MapSeriesMember = {
      member_id: memberForm.memberId.trim(),
      title: memberForm.title.trim(),
      session_id: memberForm.sessionId.trim(),
      role: memberForm.role,
      dimension_tags: splitList(memberForm.tags),
      locked_fields: splitList(memberForm.locked),
      enabled: memberForm.enabled,
    };
    try {
      await upsertMapSeriesMember(seriesId, member);
      setMemberForm(EMPTY_MEMBER_FORM);
      setNotice({ ok: true, text: t('series.saveMember') });
      setSeriesDetail(await getMapSeries(seriesId));
      await loadSeries(true);
    } catch (err) {
      setNotice({ ok: false, text: `${t('series.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setBusy(false);
    }
  };

  const removeMember = async (memberId: string) => {
    const seriesId = selectedSeriesId;
    if (!seriesId) return;
    setBusy(true);
    setNotice(null);
    try {
      await removeMapSeriesMember(seriesId, memberId);
      if (selectedMemberId === memberId) setSelectedMemberId(null);
      setNotice({ ok: true, text: t('series.removeMember') });
      setSeriesDetail(await getMapSeries(seriesId));
      await loadSeries(true);
    } catch (err) {
      setNotice({ ok: false, text: `${t('series.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setBusy(false);
    }
  };

  // ── 传播门禁：目标系列必须先跑一致性且无 error 级 findings ──
  const errorFindings = consistency?.findings.filter((f) => f.severity === 'error') ?? [];
  const consistencyCurrent = consistency !== null && consistencySeriesId === propSeriesId;
  const applyBlockedReason = !propSeriesId || !consistencyCurrent
    ? t('propagation.consistencyFirst')
    : errorFindings.length > 0 ? t('propagation.errorFindingsBlock') : null;
  const applyDisabled = applyBusy || applyBlockedReason !== null;

  const runPropPreview = async () => {
    if (!propSeriesId) return;
    setPropPreviewLoading(true);
    setPropPreview(null);
    setNotice(null);
    try {
      setPropPreview(await previewPropagation(propSeriesId, {
        spec_version: propVersion === 'latest' ? null : Number(propVersion),
        intents: {},
      }));
    } catch (err) {
      setNotice({ ok: false, text: `${t('propagation.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setPropPreviewLoading(false);
    }
  };

  const runApply = async () => {
    if (!propSeriesId || applyDisabled) return;
    setApplyBusy(true);
    setNotice(null);
    try {
      const out = await applyPropagation(propSeriesId, {
        spec_version: propVersion === 'latest' ? null : Number(propVersion),
        intents: {},
      });
      setReceipts(out.receipts);
      setApplyArmed(false);
      setNotice({ ok: true, text: t('propagation.applyDone') });
    } catch (err) {
      setNotice({ ok: false, text: `${t('propagation.requestFailed')}: ${asDesignSpecError(err).message}` });
    } finally {
      setApplyBusy(false);
    }
  };

  const members = seriesDetail?.payload.members ?? [];
  const propSpec = (specs ?? []).find((s) => s.id === propSpecId) ?? null;
  const versionOptions = propSpec
    ? Array.from({ length: Math.max(0, propSpec.current_version) }, (_, i) => String(i + 1))
    : [];

  // 传播 preview 三列投影（真实形状：members[] 计划 + unresolvable ids）。
  const affectedMembers = (propPreview?.members ?? [])
    .filter((m) => (m.changes?.length ?? 0) > 0)
    .map((m) => m.member_id);
  const lockedFieldHits = (propPreview?.members ?? [])
    .flatMap((m) => (m.locked_fields_hit ?? []).map((f) => `${m.member_id}:${f}`));
  const unresolvableMembers = propPreview?.unresolvable_member_ids ?? [];

  const tabButton = (key: TabKey, label: string) => (
    <button key={key} type="button" role="tab" aria-selected={tab === key}
            data-testid={`design-series-tab-${key}`}
            className={`rounded px-2 py-1 text-meta font-medium ${
              tab === key ? 'bg-surface-raised text-ink shadow-sm border border-edge-subtle'
                : 'text-ink-muted border border-transparent'
            }`}
            onClick={() => switchTab(key)}>
      {label}
    </button>
  );

  const severityBadge = (severity: string) => (
    <span className="rounded border px-1 text-[10px] font-medium" data-severity={severity}
          data-testid={`design-series-severity-${severity}`}>
      {severity === 'error' ? t('series.severityError')
        : severity === 'warning' ? t('series.severityWarning') : t('series.severityInfo')}
    </span>
  );

  const receiptBadge = (status: string) => (
    <span className="rounded border px-1 text-[10px] font-medium" data-status={status}>
      {status === 'committed' ? t('propagation.receiptCommitted')
        : status === 'superseded' ? t('propagation.receiptSuperseded')
          : status === 'skipped_locked' ? t('propagation.receiptSkippedLocked')
            : t('propagation.receiptFailed')}
    </span>
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3 text-body" data-testid="design-series-panel">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-subtitle font-semibold text-ink">{t('title')}</h2>
          <p className="text-meta text-ink-muted">{t('subtitle')}</p>
        </div>
        {onClose && (
          <button type="button" className="btn btn-ghost text-meta" onClick={onClose}>
            {t('close')}
          </button>
        )}
      </header>

      <div role="tablist" aria-label={t('title')}
           className="flex gap-1 rounded-md border border-edge-subtle bg-surface-sunken p-0.5">
        {tabButton('specs', t('tabs.specs'))}
        {tabButton('series', t('tabs.series'))}
        {tabButton('propagation', t('tabs.propagation'))}
      </div>

      {/* 窄屏单列折叠、宽屏双列 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-2"
           data-testid="design-series-grid">

        {tab === 'specs' && (
          <>
            {/* 左列：规范列表 + 新建表单 */}
            <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                     aria-label={t('specs.listTitle')}>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-meta font-semibold text-ink">{t('specs.listTitle')}</h3>
                <div className="flex gap-1">
                  <button type="button" className="btn btn-ghost text-meta"
                          data-testid="design-series-specs-reload"
                          onClick={() => void loadSpecs(true)}>
                    {t('common.reload')}
                  </button>
                  <button type="button" className="btn btn-ghost text-meta"
                          data-testid="design-series-create-toggle"
                          onClick={() => setShowCreate((v) => !v)}>
                    {t('specs.createToggle')}
                  </button>
                </div>
              </div>

              {showCreate && (
                <div className="mb-2 space-y-1 rounded border border-edge-subtle bg-surface-sunken p-2"
                     data-testid="design-series-create-form">
                  <p className="text-meta font-semibold text-ink">{t('specs.createTitle')}</p>
                  <label className="block text-meta text-ink-muted">
                    {t('specs.fieldName')}
                    <input className="input input-sm w-full" data-testid="design-series-create-name"
                           value={form.name} placeholder={t('specs.fieldNamePh')}
                           onChange={(e) => setForm({ ...form, name: e.target.value })} />
                  </label>
                  <label className="block text-meta text-ink-muted">
                    {t('specs.fieldDescription')}
                    <input className="input input-sm w-full" value={form.description}
                           placeholder={t('specs.fieldDescriptionPh')}
                           onChange={(e) => setForm({ ...form, description: e.target.value })} />
                  </label>
                  <div className="grid grid-cols-2 gap-1">
                    <label className="block text-meta text-ink-muted">
                      {t('specs.fieldTheme')}
                      <input className="input input-sm" value={form.themeId}
                             onChange={(e) => setForm({ ...form, themeId: e.target.value })} />
                    </label>
                    <label className="block text-meta text-ink-muted">
                      {t('specs.fieldPalette')}
                      <input className="input input-sm" value={form.palette}
                             onChange={(e) => setForm({ ...form, palette: e.target.value })} />
                    </label>
                    <label className="block text-meta text-ink-muted">
                      {t('specs.fieldMethod')}
                      <input className="input input-sm" value={form.method}
                             onChange={(e) => setForm({ ...form, method: e.target.value })} />
                    </label>
                    <label className="block text-meta text-ink-muted">
                      {t('specs.fieldTargets')}
                      <input className="input input-sm" value={form.targets}
                             onChange={(e) => setForm({ ...form, targets: e.target.value })} />
                    </label>
                  </div>
                  <label className="flex items-center gap-1 text-meta text-ink-muted">
                    <input type="checkbox" checked={form.sharedRequired}
                           onChange={(e) => setForm({ ...form, sharedRequired: e.target.checked })} />
                    {t('specs.fieldShared')}
                  </label>
                  <button type="button" className="btn btn-primary w-full text-meta"
                          data-testid="design-series-create-submit"
                          disabled={busy || !form.name.trim()} onClick={() => void submitCreate()}>
                    {t('specs.createSubmit')}
                  </button>
                  {createViolations.length > 0 && (
                    <div className="rounded border border-danger/40 bg-danger/5 p-1.5"
                         data-testid="design-series-create-violations">
                      <ViolationList items={createViolations} />
                    </div>
                  )}
                </div>
              )}

              {specsLoading && <p className="text-meta text-ink-muted">{t('common.loading')}</p>}
              {specsError && <p className="text-meta text-danger">{specsError}</p>}
              {!specsLoading && specs !== null && specs.length === 0 && (
                <p className="text-meta text-ink-muted" data-testid="design-series-specs-empty">
                  {t('specs.emptyList')}
                </p>
              )}
              {(specs ?? []).map((s) => (
                <button key={s.id} type="button"
                        className={`mb-1 w-full rounded border px-2 py-1 text-left text-meta ${
                          selectedSpecId === s.id ? 'border-accent bg-surface-raised' : 'border-edge-subtle'
                        }`}
                        data-testid={`design-series-spec-item-${s.id}`}
                        data-status={s.status}
                        onClick={() => void selectSpec(s.id)}>
                  <span className="font-medium text-ink">{s.name}</span>
                  <span className="ml-1 text-ink-muted">
                    · {specStatusLabel(s.status)} · v{s.current_version} · #{digestShort(s.structural_digest)}
                  </span>
                  {s.is_builtin && (
                    <span className="ml-1 rounded border border-edge-subtle px-1 text-[10px]">
                      {t('specs.builtinTag')}
                    </span>
                  )}
                </button>
              ))}
            </section>

            {/* 右列：详情 / 分节 / 校验 / 预览 / 操作 */}
            <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                     aria-label={t('specs.detailTitle')} data-testid="design-series-spec-detail">
              {!selectedSpecId && <p className="text-meta text-ink-muted">{t('specs.emptyDetail')}</p>}
              {selectedSpecId && specDetailLoading && (
                <p className="text-meta text-ink-muted">{t('common.loading')}</p>
              )}
              {selectedSpecId && specDetail && (
                <div className="space-y-2">
                  <h3 className="text-meta font-semibold text-ink">
                    {t('specs.detailTitle')} · {specDetail.spec.name}
                  </h3>
                  {specDetail.spec.retired && (
                    <p className="rounded border border-edge-subtle bg-surface-sunken p-1.5 text-meta text-ink-muted"
                       data-testid="design-series-retired-notice">
                      {t('specs.retiredNotice')}
                    </p>
                  )}
                  {/* payload 分节原样披露（开放结构 → 值 JSON 序列化；不造结构） */}
                  <div className="space-y-1" data-testid="design-series-payload-sections">
                    {([
                      ['specs.sectionColor', specDetail.payload.color],
                      ['specs.sectionClassification', specDetail.payload.classification],
                      ['specs.sectionOutput', specDetail.payload.output],
                      ['specs.sectionComponents', specDetail.payload.components],
                      ['specs.sectionRoles', specDetail.payload.roles],
                      ['specs.sectionOverrides', specDetail.payload.overrides],
                    ] as Array<[string, unknown]>).map(([sectionKey, value]) => (
                      <div key={sectionKey} className="rounded border border-edge-subtle p-1.5">
                        <p className="text-meta font-semibold text-ink">{t(sectionKey)}</p>
                        {value === undefined || value === null ? (
                          <p className="text-meta text-ink-muted">{t('common.none')}</p>
                        ) : (
                          <pre className="overflow-auto whitespace-pre-wrap break-all text-[10px] leading-snug text-ink-secondary">
                            {JSON.stringify(value, null, 1)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* 引用校验：违规/警告如实披露 */}
                  <div className="rounded border border-edge-subtle p-2" data-testid="design-series-validation">
                    <div className="mb-1 flex items-center justify-between">
                      <h4 className="text-meta font-semibold text-ink">{t('specs.validationTitle')}</h4>
                      <span className={`text-meta ${specDetail.reference_validation.ok ? 'text-emerald-600' : 'text-danger'}`}
                            data-testid="design-series-validation-ok"
                            data-ok={specDetail.reference_validation.ok ? 'true' : 'false'}>
                        {specDetail.reference_validation.ok
                          ? t('specs.validationOk') : t('specs.validationFailed')}
                      </span>
                    </div>
                    {specDetail.reference_validation.violations.length === 0
                      && specDetail.reference_validation.warnings.length === 0 && (
                      <p className="text-meta text-ink-muted">{t('specs.noViolations')}</p>
                    )}
                    {specDetail.reference_validation.violations.length > 0 && (
                      <div className="mt-1 rounded border border-danger/40 bg-danger/5 p-1.5"
                           data-testid="design-series-violations">
                        <p className="mb-0.5 text-meta font-semibold text-danger">{t('specs.violationsTitle')}</p>
                        <ViolationList items={specDetail.reference_validation.violations} />
                      </div>
                    )}
                    {specDetail.reference_validation.warnings.length > 0 && (
                      <div className="mt-1 rounded border border-edge-subtle bg-surface-sunken p-1.5"
                           data-testid="design-series-warnings">
                        <p className="mb-0.5 text-meta font-semibold text-ink">{t('specs.warningsTitle')}</p>
                        <ViolationList items={specDetail.reference_validation.warnings} />
                      </div>
                    )}
                  </div>

                  <div className="rounded border border-edge-subtle p-2" data-testid="design-series-preview">
                    <div className="mb-1 flex items-center justify-between">
                      <h4 className="text-meta font-semibold text-ink">{t('specs.previewTitle')}</h4>
                      <button type="button" className="btn btn-ghost text-meta"
                              data-testid="design-series-preview-run"
                              disabled={previewLoading} onClick={() => void runSpecPreview()}>
                        {t('specs.previewAction')}
                      </button>
                    </div>
                    {!preview && (
                      <p className="text-meta text-ink-muted">{t('specs.previewEmpty')}</p>
                    )}
                    {preview && (
                      <div className="space-y-1 text-meta">
                        <p>
                          <span className="font-semibold">{t('specs.targetsSupported')}</span>
                          <span data-testid="design-series-targets-supported">
                            {' '}{preview.render_targets.supported.join(', ')}
                          </span>
                        </p>
                        <p>
                          <span className="font-semibold">{t('specs.targetsUnsupported')}</span>
                          {preview.render_targets.unsupported.length === 0 ? (
                            <span data-testid="design-series-targets-unsupported"> {t('common.none')}</span>
                          ) : (
                            <ul className="list-none" data-testid="design-series-targets-unsupported">
                              {preview.render_targets.unsupported.map((u) => (
                                <li key={u.target}>{u.target} — {u.reason}</li>
                              ))}
                            </ul>
                          )}
                        </p>
                        <p>
                          <span className="font-semibold">{t('specs.conflictsTitle')}</span>
                          {preview.conflicts.length === 0 ? (
                            <span data-testid="design-series-conflicts"> {t('specs.conflictsEmpty')}</span>
                          ) : (
                            <ul className="list-none" data-testid="design-series-conflicts">
                              {preview.conflicts.map((c, i) => (
                                <li key={`${c.code}-${i}`}>{c.code}{c.message ? ` — ${c.message}` : ''}</li>
                              ))}
                            </ul>
                          )}
                        </p>
                        {preview.render_targets.font_findings.length > 0 && (
                          <p>
                            <span className="font-semibold">{t('specs.fontFindingsTitle')}</span>
                            <span> {preview.render_targets.font_findings.length}</span>
                          </p>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-1 rounded border border-edge-subtle p-2">
                    <button type="button" className="btn btn-ghost text-meta"
                            data-testid="design-series-version-run"
                            disabled={busy} onClick={() => void runVersion()}>
                      {t('specs.versionAction')}
                    </button>
                    <button type="button" className="btn btn-ghost text-meta"
                            data-testid="design-series-publish-run"
                            disabled={busy} onClick={() => void runPublish()}>
                      {t('specs.publishAction')}
                    </button>
                    <label className="flex items-center gap-1 text-meta text-ink-muted">
                      {t('specs.retireNote')}
                      <input className="input input-sm w-32" value={retireNote}
                             placeholder={t('specs.retireNotePh')}
                             onChange={(e) => setRetireNote(e.target.value)} />
                    </label>
                    <button type="button" className="btn btn-ghost text-meta"
                            data-testid="design-series-retire-run"
                            disabled={busy} onClick={() => void runRetire()}>
                      {t('specs.retireAction')}
                    </button>
                  </div>
                </div>
              )}
            </section>
          </>
        )}

        {tab === 'series' && (
          <>
            {/* 左列：系列列表 + 成员表单 */}
            <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                     aria-label={t('series.listTitle')}>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-meta font-semibold text-ink">{t('series.listTitle')}</h3>
                <div className="flex gap-1">
                  <button type="button" className="btn btn-ghost text-meta"
                          data-testid="design-series-series-reload"
                          onClick={() => void loadSeries(true)}>
                    {t('common.reload')}
                  </button>
                  <button type="button" className="btn btn-ghost text-meta"
                          data-testid="design-series-member-add"
                          disabled={!selectedSeriesId}
                          onClick={() => setMemberForm({ ...EMPTY_MEMBER_FORM, open: true })}>
                    {t('series.addMember')}
                  </button>
                </div>
              </div>

              {seriesLoading && <p className="text-meta text-ink-muted">{t('common.loading')}</p>}
              {seriesError && <p className="text-meta text-danger">{seriesError}</p>}
              {!seriesLoading && series !== null && series.length === 0 && (
                <p className="text-meta text-ink-muted" data-testid="design-series-series-empty">
                  {t('series.emptyList')}
                </p>
              )}
              {(series ?? []).map((s) => (
                <button key={s.id} type="button"
                        className={`mb-1 w-full rounded border px-2 py-1 text-left text-meta ${
                          selectedSeriesId === s.id ? 'border-accent bg-surface-raised' : 'border-edge-subtle'
                        }`}
                        data-testid={`design-series-series-item-${s.id}`}
                        onClick={() => void selectSeries(s.id)}>
                  <span className="font-medium text-ink">{s.name}</span>
                  <span className="ml-1 text-ink-muted">
                    · {specStatusLabel(s.status)} · v{s.current_version}
                  </span>
                </button>
              ))}

              {memberForm.open && (
                <div className="mt-2 space-y-1 rounded border border-edge-subtle bg-surface-sunken p-2"
                     data-testid="design-series-member-form">
                  <p className="text-meta font-semibold text-ink">
                    {memberForm.editing ? t('series.editMember') : t('series.addMember')}
                  </p>
                  <label className="block text-meta text-ink-muted">
                    {t('series.fieldMemberId')}
                    <input className="input input-sm w-full" data-testid="design-series-member-id"
                           value={memberForm.memberId} placeholder={t('series.fieldMemberIdPh')}
                           onChange={(e) => setMemberForm({ ...memberForm, memberId: e.target.value })} />
                  </label>
                  <label className="block text-meta text-ink-muted">
                    {t('series.fieldTitle')}
                    <input className="input input-sm w-full" value={memberForm.title}
                           placeholder={t('series.fieldTitlePh')}
                           onChange={(e) => setMemberForm({ ...memberForm, title: e.target.value })} />
                  </label>
                  <label className="block text-meta text-ink-muted">
                    {t('series.fieldSession')}
                    <input className="input input-sm w-full" value={memberForm.sessionId}
                           placeholder={t('series.fieldSessionPh')}
                           onChange={(e) => setMemberForm({ ...memberForm, sessionId: e.target.value })} />
                  </label>
                  <div className="grid grid-cols-2 gap-1">
                    <label className="block text-meta text-ink-muted">
                      {t('series.fieldRole')}
                      <select className="input input-sm" value={memberForm.role}
                              onChange={(e) => setMemberForm({
                                ...memberForm, role: e.target.value as SeriesMemberRole,
                              })}>
                        {MEMBER_ROLES.map((r) => (
                          <option key={r} value={r}>{roleLabel(r)}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-meta text-ink-muted">
                      {t('series.fieldTags')}
                      <input className="input input-sm" value={memberForm.tags}
                             placeholder={t('series.fieldTagsPh')}
                             onChange={(e) => setMemberForm({ ...memberForm, tags: e.target.value })} />
                    </label>
                  </div>
                  <label className="block text-meta text-ink-muted">
                    {t('series.fieldLocked')}
                    <input className="input input-sm w-full" value={memberForm.locked}
                           placeholder={t('series.fieldLockedPh')}
                           onChange={(e) => setMemberForm({ ...memberForm, locked: e.target.value })} />
                  </label>
                  <label className="flex items-center gap-1 text-meta text-ink-muted">
                    <input type="checkbox" checked={memberForm.enabled}
                           onChange={(e) => setMemberForm({ ...memberForm, enabled: e.target.checked })} />
                    {t('series.fieldEnabled')}
                  </label>
                  <div className="flex gap-1">
                    <button type="button" className="btn btn-primary flex-1 text-meta"
                            data-testid="design-series-member-save"
                            disabled={busy || !memberForm.memberId.trim()}
                            onClick={() => void saveMember()}>
                      {t('series.saveMember')}
                    </button>
                    <button type="button" className="btn btn-ghost text-meta"
                            onClick={() => setMemberForm(EMPTY_MEMBER_FORM)}>
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </section>

            {/* 右列：成员卡网格 + 一致性 findings */}
            <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                     aria-label={t('series.detailTitle')} data-testid="design-series-detail">
              {!selectedSeriesId && (
                <p className="text-meta text-ink-muted">{t('series.emptySelection')}</p>
              )}
              {selectedSeriesId && seriesDetailLoading && (
                <p className="text-meta text-ink-muted">{t('common.loading')}</p>
              )}
              {selectedSeriesId && seriesDetail && (
                <div className="space-y-2">
                  <h3 className="text-meta font-semibold text-ink">
                    {t('series.detailTitle')} · {seriesDetail.payload.name}
                  </h3>
                  {seriesDetail.payload.design_spec_id && (
                    <p className="text-meta text-ink-muted" data-testid="design-series-spec-pin">
                      {t('series.specPin')}: {seriesDetail.payload.design_spec_id}
                    </p>
                  )}

                  <div>
                    <h4 className="mb-1 text-meta font-semibold text-ink">
                      {t('series.membersTitle')} · {members.length}
                    </h4>
                    {members.length === 0 && (
                      <p className="text-meta text-ink-muted" data-testid="design-series-members-empty">
                        {t('series.cardsEmpty')}
                      </p>
                    )}
                    <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                      {members.map((m) => (
                        <div key={m.member_id}
                             className={`rounded border p-1.5 text-meta ${
                               selectedMemberId === m.member_id
                                 ? 'border-accent bg-surface-raised' : 'border-edge-subtle'
                             }`}
                             data-testid={`design-series-member-card-${m.member_id}`}
                             data-selected={selectedMemberId === m.member_id ? 'true' : 'false'}
                             data-locked={m.locked_fields.length > 0 ? 'true' : 'false'}
                             data-enabled={m.enabled ? 'true' : 'false'}>
                          <button type="button" className="w-full text-left"
                                  data-testid={`design-series-member-select-${m.member_id}`}
                                  onClick={() => setSelectedMemberId(m.member_id)}>
                            <span className="font-medium text-ink">{m.title || m.member_id}</span>
                            <span className="ml-1 text-ink-muted">
                              · {roleLabel(m.role)}
                            </span>
                            {m.locked_fields.length > 0 && (
                              <span className="ml-1 rounded border border-edge-subtle px-1 text-[10px]"
                                    data-testid="design-series-locked-badge">
                                {t('series.lockedBadge')} {m.locked_fields.length}
                              </span>
                            )}
                            {!m.enabled && (
                              <span className="ml-1 text-[10px] text-ink-muted">
                                {t('series.tagDisabled')}
                              </span>
                            )}
                            <span className="mt-0.5 block text-[10px] text-ink-muted">
                              {m.dimension_tags.join(' · ')}
                            </span>
                          </button>
                          {selectedMemberId === m.member_id && (
                            <div className="mt-1 space-y-1 border-t border-edge-subtle pt-1"
                                 data-testid="design-series-member-detail">
                              <p className="text-[10px] text-ink-muted" data-testid="design-series-open-in-map">
                                {t('series.openInMap')} — {t('series.sessionRef')}: {m.session_id}
                                <span className="ml-1">({t('series.openInMapHint')})</span>
                              </p>
                              <div className="flex gap-1">
                                <button type="button" className="btn btn-ghost px-1.5 py-0.5 text-[10px]"
                                        onClick={() => setMemberForm({
                                          open: true, editing: m.member_id,
                                          memberId: m.member_id, title: m.title,
                                          sessionId: m.session_id, role: m.role,
                                          tags: m.dimension_tags.join(','),
                                          locked: m.locked_fields.join(','),
                                          enabled: m.enabled,
                                        })}>
                                  {t('series.editMember')}
                                </button>
                                <button type="button" className="btn btn-ghost px-1.5 py-0.5 text-[10px]"
                                        data-testid={`design-series-member-remove-${m.member_id}`}
                                        disabled={busy} onClick={() => void removeMember(m.member_id)}>
                                  {t('series.removeMember')}
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded border border-edge-subtle p-2" data-testid="design-series-consistency">
                    <div className="mb-1 flex items-center justify-between">
                      <h4 className="text-meta font-semibold text-ink">{t('series.consistencyTitle')}</h4>
                      <button type="button" className="btn btn-ghost text-meta"
                              data-testid="design-series-consistency-run"
                              disabled={consistencyLoading}
                              onClick={() => void runConsistency(selectedSeriesId)}>
                        {t('series.consistencyAction')}
                      </button>
                    </div>
                    {consistencyLoading && (
                      <p className="text-meta text-ink-muted">{t('common.loading')}</p>
                    )}
                    {!consistencyLoading && consistency === null && (
                      <p className="text-meta text-ink-muted" data-testid="design-series-findings-empty">
                        {t('series.findingsEmpty')}
                      </p>
                    )}
                    {!consistencyLoading && consistency !== null && (
                      <div data-testid="design-series-findings">
                        {consistency.findings.length === 0 && (
                          <p className="text-meta text-emerald-600">{t('series.consistencyOk')}</p>
                        )}
                        <ul className="list-none space-y-1">
                          {consistency.findings.map((f, i) => (
                            <li key={`${f.code}-${i}`}
                                className="rounded border border-edge-subtle p-1"
                                data-testid={`design-series-finding-${f.code}`}>
                              {severityBadge(f.severity)}
                              <span className="ml-1 font-medium">{f.code}</span>
                              <span className="ml-1 text-ink-muted">
                                {f.member_id ? `· ${f.member_id}` : ''}{f.component ? ` · ${f.component}` : ''}
                              </span>
                              <span className="block text-[10px] text-ink-secondary">{f.message}</span>
                              {f.fix_hint && (
                                <span className="block text-[10px] text-ink-muted">{f.fix_hint}</span>
                              )}
                              {f.member_id && members.some((m) => m.member_id === f.member_id) && (
                                <button type="button" className="btn btn-ghost mt-0.5 px-1.5 py-0.5 text-[10px]"
                                        onClick={() => setSelectedMemberId(f.member_id ?? null)}>
                                  {t('series.locateMember')}
                                </button>
                              )}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </section>
          </>
        )}

        {tab === 'propagation' && (
          <>
            {/* 左列：选择 + preview 三列 */}
            <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                     aria-label={t('propagation.title')}>
              <h3 className="mb-2 text-meta font-semibold text-ink">{t('propagation.title')}</h3>
              <div className="mb-2 space-y-1">
                <label className="block text-meta text-ink-muted">
                  {t('propagation.specLabel')}
                  <select className="input input-sm w-full" value={propSpecId}
                          data-testid="design-series-prop-spec"
                          onChange={(e) => { setPropSpecId(e.target.value); setReceipts(null); }}>
                    <option value="">{t('propagation.emptyOption')}</option>
                    {(specs ?? []).map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-meta text-ink-muted">
                  {t('propagation.versionLabel')}
                  <select className="input input-sm w-full" value={propVersion}
                          data-testid="design-series-prop-version"
                          disabled={!propSpec}
                          onChange={(e) => { setPropVersion(e.target.value); setReceipts(null); }}>
                    <option value="latest">{t('propagation.versionLatest')}</option>
                    {versionOptions.map((v) => (
                      <option key={v} value={v}>v{v}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-meta text-ink-muted">
                  {t('propagation.seriesLabel')}
                  <select className="input input-sm w-full" value={propSeriesId}
                          data-testid="design-series-prop-series"
                          onChange={(e) => {
                            setPropSeriesId(e.target.value);
                            setReceipts(null);
                            setApplyArmed(false);
                            void runConsistency(e.target.value);
                          }}>
                    <option value="">{t('propagation.emptyOption')}</option>
                    {(series ?? []).map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </label>
              </div>

              <button type="button" className="btn btn-ghost w-full text-meta"
                      data-testid="design-series-prop-preview-run"
                      disabled={!propSeriesId || propPreviewLoading}
                      onClick={() => void runPropPreview()}>
                {t('propagation.previewAction')}
              </button>

              <div className="mt-2 rounded border border-edge-subtle p-2"
                   data-testid="design-series-prop-preview">
                <h4 className="mb-1 text-meta font-semibold text-ink">{t('propagation.previewTitle')}</h4>
                {!propPreview && !propPreviewLoading && (
                  <p className="text-meta text-ink-muted">{t('propagation.previewEmpty')}</p>
                )}
                {propPreviewLoading && <p className="text-meta text-ink-muted">{t('common.loading')}</p>}
                {propPreview && (
                  <div className="grid grid-cols-1 gap-1 sm:grid-cols-3">
                    {([
                      { key: 'affected', title: t('propagation.colAffected'), items: affectedMembers },
                      { key: 'locked', title: t('propagation.colLocked'), items: lockedFieldHits },
                      { key: 'unresolvable', title: t('propagation.colUnresolvable'), items: unresolvableMembers },
                    ]).map((col) => (
                      <div key={col.key} className="rounded border border-edge-subtle p-1"
                           data-testid={`design-series-prop-col-${col.key}`}>
                        <p className="text-meta font-semibold text-ink">{col.title}</p>
                        {col.items.length === 0 ? (
                          <p className="text-[10px] text-ink-muted">{t('propagation.emptyCol')}</p>
                        ) : (
                          <ul className="list-none text-[10px]">
                            {col.items.map((item) => <li key={item}>{item}</li>)}
                          </ul>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            {/* 右列：一致性门禁 + apply + receipts */}
            <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                     aria-label={t('propagation.applyAction')} data-testid="design-series-prop-apply-section">
              <div className="mb-2 rounded border border-edge-subtle p-2"
                   data-testid="design-series-prop-gate">
                <div className="mb-1 flex items-center justify-between">
                  <h4 className="text-meta font-semibold text-ink">{t('series.consistencyTitle')}</h4>
                  <button type="button" className="btn btn-ghost text-meta"
                          data-testid="design-series-prop-rerun"
                          disabled={!propSeriesId || consistencyLoading}
                          onClick={() => void runConsistency(propSeriesId)}>
                    {t('propagation.rerunConsistency')}
                  </button>
                </div>
                {!consistencyCurrent && (
                  <p className="text-meta text-ink-muted" data-testid="design-series-prop-gate-hint">
                    {t('propagation.consistencyFirst')}
                  </p>
                )}
                {consistencyCurrent && errorFindings.length > 0 && (
                  <p className="text-meta text-danger" data-testid="design-series-prop-gate-hint">
                    {t('propagation.errorFindingsBlock')} ({errorFindings.length})
                  </p>
                )}
                {consistencyCurrent && errorFindings.length === 0 && (
                  <p className="text-meta text-emerald-600">{t('series.consistencyOk')}</p>
                )}
              </div>

              {applyArmed ? (
                <div className="mb-2 rounded border border-edge-subtle bg-surface-sunken p-2"
                     data-testid="design-series-prop-confirm">
                  <p className="text-meta font-semibold text-ink">{t('propagation.confirmTitle')}</p>
                  <p className="mb-1 text-[10px] text-ink-muted">{t('propagation.confirmHint')}</p>
                  <div className="flex gap-1">
                    <button type="button" className="btn btn-primary flex-1 text-meta"
                            data-testid="design-series-prop-confirm-run"
                            disabled={applyBusy} onClick={() => void runApply()}>
                      {t('propagation.confirmApply')}
                    </button>
                    <button type="button" className="btn btn-ghost text-meta"
                            onClick={() => setApplyArmed(false)}>
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" className="btn btn-primary w-full text-meta"
                        data-testid="design-series-prop-apply"
                        disabled={applyDisabled}
                        onClick={() => setApplyArmed(true)}>
                  {t('propagation.applyAction')}
                </button>
              )}
              {applyBlockedReason && !applyArmed && (
                <p className="mt-1 text-meta text-ink-muted">{applyBlockedReason}</p>
              )}

              {receipts !== null && (
                <div className="mt-2" data-testid="design-series-receipts">
                  <h4 className="mb-1 text-meta font-semibold text-ink">{t('propagation.receiptsTitle')}</h4>
                  {receipts.length === 0 && (
                    <p className="text-meta text-ink-muted">{t('common.empty')}</p>
                  )}
                  <ul className="list-none space-y-1">
                    {receipts.map((r) => (
                      <li key={r.member_id} className="rounded border border-edge-subtle p-1 text-meta"
                          data-testid={`design-series-receipt-${r.member_id}`}>
                        {receiptBadge(r.status)}
                        <span className="ml-1 font-medium">{r.member_id}</span>
                        {r.revision !== undefined && r.revision !== null && (
                          <span className="ml-1 text-ink-muted">r{String(r.revision)}</span>
                        )}
                        {r.error && (
                          <span className="block text-[10px] text-danger">
                            {t('propagation.receiptError')}: {r.error}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          </>
        )}
      </div>

      <footer className="flex items-center gap-2 border-t border-edge-subtle pt-2">
        <span className="flex-1" />
        <span className="text-meta text-ink-muted" aria-live="polite" data-testid="design-series-notice">
          {notice?.text ?? ''}
        </span>
      </footer>
    </div>
  );
}

/**
 * Studio 侧薄入口：按钮 + 全屏弹层（自管 open 态），map-studio-tab 只需
 * 1 import + 1 挂载（放在 print-layout-panel 挂载点之后，便于 rebase 核对）。
 */
export function DesignSeriesLauncher() {
  const t = useT('designSeries');
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button"
              className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-1.5 text-meta font-medium text-ink hover:bg-surface-raised"
              data-testid="open-design-series-workbench"
              onClick={() => setOpen(true)}>
        {t('openWorkbench')}
      </button>
      {open && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4"
             role="dialog" aria-modal="true"
             aria-label={t('title')}
             data-testid="design-series-modal">
          <div className="flex h-[92vh] w-[94vw] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-overlay shadow-2xl">
            <DesignSeriesPanel onClose={() => setOpen(false)} />
          </div>
        </div>
      )}
    </>
  );
}
