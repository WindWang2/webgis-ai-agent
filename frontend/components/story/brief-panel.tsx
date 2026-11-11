'use client';

/**
 * G11 证据化地图简报面板（ADR-0288）—— 草稿/编辑/证据/预检/发布/分享。
 *
 * 独立于 StoryMap specView 的简报数据面：块级编辑（锁定/删除/正文）、
 * 指标卡证据核对（BRF 指令 2）、预检 verdict 列表、冻结 release、私有
 * 下载与显式分享。乐观 revision：409 冲突 → toast + 刷新（不吞人工内容）。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ClipboardCheck, Download, FileLock2, Lock, LockOpen, Plus, RefreshCw,
  Share2, ShieldOff, Trash2, X,
} from 'lucide-react';

import { useT } from '@/lib/i18n/useT';
import { ApiError, describeApiError } from '@/lib/api/transport';
import { useToastStore } from '@/components/ui/toast';
import {
  type BriefBlockDto, type BriefResponseDto, type EvidenceInspectionDto,
  type BriefReleaseDto, type PreflightReportDto,
  createBriefDraft, deleteBriefBlock, downloadBriefReleaseHtml, freezeBriefRelease,
  getBrief, inspectBriefEvidence, listBriefReleases, patchBriefBlock,
  regenerateBrief, revokeBriefRelease, runBriefPreflight, shareBriefRelease,
} from '@/lib/api/brief';

interface BriefPanelProps {
  sessionId: string;
  /** 匿名会话 owner token 通道（story-token-handoff）。 */
  onRequestOwnerToken: (sessionId: string) => Promise<string | null>;
  onClose: () => void;
}

const STATUS_KIND: Record<string, string> = {
  verified: 'text-status-success',
  warning: 'text-status-warning',
  unverified: 'text-status-error',
};

const VERDICT_KIND: Record<string, string> = {
  pass: 'text-status-success',
  fail: 'text-status-error',
  not_evaluated: 'text-status-warning',
};

export function BriefPanel(props: BriefPanelProps): React.ReactElement {
  const { sessionId, onRequestOwnerToken, onClose } = props;
  const t = useT('story');
  const [brief, setBrief] = useState<BriefResponseDto | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState('');
  const [evidence, setEvidence] = useState<EvidenceInspectionDto | null>(null);
  const [preflight, setPreflight] = useState<PreflightReportDto | null>(null);
  const [releases, setReleases] = useState<BriefReleaseDto[]>([]);
  const [allowDraftFreeze, setAllowDraftFreeze] = useState(false);

  const ownerToken = useCallback(
    () => onRequestOwnerToken(sessionId), [onRequestOwnerToken, sessionId]);

  const reload = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setBusy(true);
    try {
      const token = await ownerToken();
      const data = await getBrief(sessionId, { ownerToken: token });
      setBrief(data);
      setMissing(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setBrief(null);
        setMissing(true);
      } else {
        useToastStore.getState().addToast(describeApiError(err, t('briefLoadFailed')), 'error');
      }
    } finally {
      setBusy(false);
    }
  }, [ownerToken, sessionId, t]);

  useEffect(() => { void reload(); }, [reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleConflict = useCallback(async (err: unknown) => {
    if (err instanceof ApiError && err.status === 409) {
      useToastStore.getState().addToast(t('briefRevisionConflict'), 'warning');
      await reload();
      return true;
    }
    return false;
  }, [reload, t]);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      const token = await ownerToken();
      await createBriefDraft(sessionId, {}, { ownerToken: token });
      useToastStore.getState().addToast(t('briefCreated'), 'success');
      await reload({ silent: true });
    } catch (err) {
      useToastStore.getState().addToast(describeApiError(err, t('briefCreateFailed')), 'error');
    } finally {
      setCreating(false);
    }
  }, [ownerToken, reload, sessionId, t]);

  /** 块级写操作统一壳：带当前 revision、409 处理、成功后同步本地 spec。 */
  const mutate = useCallback(async (
    action: (revision: number, token: string | null) => Promise<{ revision: number; spec?: BriefResponseDto['spec'] }>,
  ) => {
    if (!brief) return;
    setBusy(true);
    try {
      const token = await ownerToken();
      const result = await action(brief.revision, token);
      if (result.spec) {
        setBrief({ ...brief, revision: result.revision, spec: result.spec });
      } else {
        await reload({ silent: true });
      }
    } catch (err) {
      if (!(await handleConflict(err))) {
        useToastStore.getState().addToast(describeApiError(err, t('briefUpdateFailed')), 'error');
      }
    } finally {
      setBusy(false);
    }
  }, [brief, handleConflict, ownerToken, reload, t]);

  const startEdit = useCallback((block: BriefBlockDto) => {
    setEditingId(block.id);
    setEditDraft(block.text ?? '');
  }, []);

  const saveEdit = useCallback((blockId: string) => {
    void mutate((revision, token) => patchBriefBlock(
      sessionId, blockId,
      // 用户编辑的正文标记 manual —— 重生成永不覆盖（BRF-O03）。
      { text: editDraft, origin: 'manual' },
      revision, { ownerToken: token },
    ).then((r) => { setEditingId(null); return r; }));
  }, [editDraft, mutate, sessionId]);

  const toggleLock = useCallback((block: BriefBlockDto) => {
    void mutate((revision, token) => patchBriefBlock(
      sessionId, block.id, { locked: !block.locked }, revision, { ownerToken: token },
    ));
  }, [mutate, sessionId]);

  const removeBlock = useCallback((blockId: string) => {
    void mutate((revision, token) => deleteBriefBlock(
      sessionId, blockId, revision, { ownerToken: token },
    ));
  }, [mutate, sessionId]);

  const moveBlock = useCallback((block: BriefBlockDto, dir: -1 | 1) => {
    if (!brief) return;
    const siblings = brief.spec.blocks
      .filter((b) => b.chapter_id === block.chapter_id)
      .sort((a, b) => a.order - b.order);
    const idx = siblings.findIndex((b) => b.id === block.id);
    const target = siblings[idx + dir];
    if (!target) return;
    // 交换 order：两次顺序 patch（revision 链式推进）。
    void mutate(async (revision, token) => {
      const first = await patchBriefBlock(
        sessionId, block.id, { order: target.order }, revision, { ownerToken: token });
      const second = await patchBriefBlock(
        sessionId, target.id, { order: block.order }, first.revision, { ownerToken: token });
      return second;
    });
  }, [brief, mutate, sessionId]);

  const regenerate = useCallback((mode: 'metrics_only' | 'unlocked_text_only') => {
    void mutate((revision, token) => regenerateBrief(sessionId, mode, revision, { ownerToken: token }));
  }, [mutate, sessionId]);

  const openEvidence = useCallback(async (factId: string) => {
    try {
      const token = await ownerToken();
      setEvidence(await inspectBriefEvidence(sessionId, factId, { ownerToken: token }));
    } catch (err) {
      useToastStore.getState().addToast(describeApiError(err, t('evidenceLoadFailed')), 'error');
    }
  }, [ownerToken, sessionId, t]);

  const runPreflight = useCallback(async () => {
    setBusy(true);
    try {
      const token = await ownerToken();
      setPreflight(await runBriefPreflight(sessionId, { ownerToken: token }));
    } catch (err) {
      useToastStore.getState().addToast(describeApiError(err, t('preflightFailed')), 'error');
    } finally {
      setBusy(false);
    }
  }, [ownerToken, sessionId, t]);

  const refreshReleases = useCallback(async () => {
    try {
      const token = await ownerToken();
      const res = await listBriefReleases(sessionId, { ownerToken: token });
      setReleases(res.releases);
    } catch { /* 静默：发布区下次动作会再拉 */ }
  }, [ownerToken, sessionId]);

  useEffect(() => { if (brief) void refreshReleases(); }, [brief, refreshReleases]);

  const freeze = useCallback(async () => {
    setBusy(true);
    try {
      const token = await ownerToken();
      const result = await freezeBriefRelease(sessionId, allowDraftFreeze, { ownerToken: token });
      useToastStore.getState().addToast(
        t('briefFrozen', { version: result.release_no }), 'success');
      setPreflight(null);
      await Promise.all([reload({ silent: true }), refreshReleases()]);
    } catch (err) {
      if (!(await handleConflict(err))) {
        useToastStore.getState().addToast(describeApiError(err, t('freezeFailed')), 'error');
      }
    } finally {
      setBusy(false);
    }
  }, [allowDraftFreeze, handleConflict, ownerToken, refreshReleases, reload, sessionId, t]);

  const download = useCallback(async (releaseId: string) => {
    try {
      const token = await ownerToken();
      const { blob, filename } = await downloadBriefReleaseHtml(sessionId, releaseId, { ownerToken: token });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename ?? `brief-release-${releaseId}.html`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      useToastStore.getState().addToast(describeApiError(err, t('downloadFailed')), 'error');
    }
  }, [ownerToken, sessionId, t]);

  const share = useCallback(async (releaseId: string) => {
    try {
      const token = await ownerToken();
      const res = await shareBriefRelease(sessionId, releaseId, 7, { ownerToken: token });
      const url = `${window.location.origin}/api/v1${res.share_url.replace('/api/v1', '')}`;
      await navigator.clipboard?.writeText(url).catch(() => undefined);
      useToastStore.getState().addToast(t('briefShared', { url }), 'success');
      await refreshReleases();
    } catch (err) {
      useToastStore.getState().addToast(describeApiError(err, t('shareFailed')), 'error');
    }
  }, [ownerToken, refreshReleases, sessionId, t]);

  const unshare = useCallback(async (releaseId: string) => {
    try {
      const token = await ownerToken();
      await revokeBriefRelease(sessionId, releaseId, { ownerToken: token });
      useToastStore.getState().addToast(t('briefUnshared'), 'success');
      await refreshReleases();
    } catch (err) {
      useToastStore.getState().addToast(describeApiError(err, t('unshareFailed')), 'error');
    }
  }, [ownerToken, refreshReleases, sessionId, t]);

  const staleByBlock = useMemo(() => {
    const staleIndex = new Map<string, string>();
    brief?.stale.forEach((s) => {
      if (s.block_id) staleIndex.set(s.block_id, s.reason);
    });
    return staleIndex;
  }, [brief]);

  return (
    <div className="flex h-full flex-col bg-surface-panel" data-testid="brief-panel">
      {/* 头部：标题 + revision + stale 提示 + 关闭 */}
      <div className="flex items-center justify-between border-b border-edge-subtle px-3 py-2">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-caption font-medium uppercase tracking-wide text-ink">
            <ClipboardCheck className="h-3.5 w-3.5 text-status-info" aria-hidden />
            {t('briefPanelTitle')}
            {brief ? (
              <span className="rounded-full border border-edge-subtle px-1.5 text-meta text-ink-muted">
                rev {brief.revision}
              </span>
            ) : null}
          </h2>
          {brief && brief.stale.length > 0 ? (
            <p className="text-meta text-status-warning" data-testid="brief-stale-hint">
              {t('briefStaleHint', { count: brief.stale.length })}
            </p>
          ) : null}
        </div>
        <button
          type="button" aria-label={t('briefCloseAria')} onClick={onClose}
          className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
        >
          <X size={14} aria-hidden />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 text-body-sm">
        {missing ? (
          <div className="space-y-2" data-testid="brief-empty">
            <p className="text-ink-secondary">{t('briefEmptyHint')}</p>
            <button
              type="button" onClick={() => void handleCreate()} disabled={creating}
              className="flex items-center gap-1.5 rounded-md border border-status-info-border px-2.5 py-1.5 text-status-info hover:bg-surface-hover disabled:opacity-40"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              {creating ? t('briefCreating') : t('briefCreateAction')}
            </button>
          </div>
        ) : null}

        {brief ? (
          <>
            {/* 章节 → 块编辑列表 */}
            {brief.spec.chapters.map((chapter) => {
              const blocks = brief.spec.blocks
                .filter((b) => b.chapter_id === chapter.id)
                .sort((a, b) => a.order - b.order);
              return (
                <section key={chapter.id} className="mb-4">
                  <h3 className="mb-1.5 text-caption font-medium text-status-info">{chapter.title}</h3>
                  <ul className="space-y-1.5">
                    {blocks.map((block) => (
                      <li
                        key={block.id}
                        data-testid={`brief-block-${block.kind}`}
                        className={`rounded-md border px-2 py-1.5 ${block.locked ? 'border-status-warning-border' : 'border-edge-subtle'}`}
                      >
                        <BlockRow
                          block={block}
                          staleReason={staleByBlock.get(block.id) ?? null}
                          editing={editingId === block.id}
                          editDraft={editDraft}
                          facts={brief.spec.facts}
                          busy={busy}
                          onEditStart={startEdit}
                          onEditChange={setEditDraft}
                          onEditSave={saveEdit}
                          onEditCancel={() => setEditingId(null)}
                          onToggleLock={toggleLock}
                          onDelete={removeBlock}
                          onMove={moveBlock}
                          onInspect={openEvidence}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}

            {/* 重生成 */}
            <div className="mb-4 flex flex-wrap gap-2 border-t border-edge-subtle pt-3">
              <button
                type="button" disabled={busy} onClick={() => regenerate('metrics_only')}
                className="flex items-center gap-1 rounded-md border border-edge-subtle px-2 py-1 text-ink-secondary hover:bg-surface-hover disabled:opacity-40"
              >
                <RefreshCw className="h-3 w-3" aria-hidden />
                {t('briefRegenMetrics')}
              </button>
              <button
                type="button" disabled={busy} onClick={() => regenerate('unlocked_text_only')}
                className="flex items-center gap-1 rounded-md border border-edge-subtle px-2 py-1 text-ink-secondary hover:bg-surface-hover disabled:opacity-40"
              >
                <RefreshCw className="h-3 w-3" aria-hidden />
                {t('briefRegenText')}
              </button>
            </div>

            {/* 预检 */}
            <section className="mb-4 border-t border-edge-subtle pt-3" data-testid="brief-preflight-section">
              <div className="mb-1.5 flex items-center gap-2">
                <h3 className="text-caption font-medium uppercase tracking-wide text-ink-muted">{t('briefPreflightTitle')}</h3>
                <button
                  type="button" onClick={() => void runPreflight()} disabled={busy}
                  className="rounded-md border border-status-info-border px-2 py-0.5 text-meta text-status-info hover:bg-surface-hover disabled:opacity-40"
                >
                  {t('briefPreflightRun')}
                </button>
              </div>
              {preflight ? (
                <div data-testid="brief-preflight-report">
                  <p
                    className={`mb-1.5 font-medium ${preflight.outcome === 'blocked' ? 'text-status-error' : preflight.outcome === 'deliverable' ? 'text-status-success' : 'text-status-warning'}`}
                    data-testid="brief-preflight-outcome"
                  >
                    {t(preflight.outcome === 'blocked' ? 'briefOutcomeBlocked' : preflight.outcome === 'deliverable' ? 'briefOutcomeDeliverable' : 'briefOutcomeDraftExportable')}
                    <span className="ml-2 font-normal text-ink-muted">
                      {t('briefFactStats', preflight.fact_stats)}
                    </span>
                  </p>
                  <ul className="space-y-1">
                    {preflight.items.map((item) => (
                      <li key={item.check} className="flex items-start gap-1.5 text-meta">
                        <span className={`font-mono ${VERDICT_KIND[item.verdict] ?? 'text-ink-muted'}`}>
                          [{item.verdict}]
                        </span>
                        <span className="text-ink-secondary">
                          {t({source_authorization:'briefCheckSourceAuthorization',reference_resolvability:'briefCheckReferenceResolvability',numeric_consistency:'briefCheckNumericConsistency',map_version_coverage:'briefCheckMapVersionCoverage',content_completeness:'briefCheckContentCompleteness',privacy_scan:'briefCheckPrivacyScan',attribution_license:'briefCheckAttributionLicense'}[item.check] ?? item.check)}
                          {item.detail ? ` — ${item.detail}` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="text-meta text-ink-muted">{t('briefPreflightHint')}</p>
              )}
            </section>

            {/* 发布 */}
            <section className="border-t border-edge-subtle pt-3" data-testid="brief-release-section">
              <h3 className="mb-1.5 text-caption font-medium uppercase tracking-wide text-ink-muted">{t('briefReleaseTitle')}</h3>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-meta text-ink-secondary">
                  <input
                    type="checkbox" checked={allowDraftFreeze}
                    onChange={(e) => setAllowDraftFreeze(e.target.checked)}
                    data-testid="brief-allow-draft"
                  />
                  {t('briefAllowDraft')}
                </label>
                <button
                  type="button" onClick={() => void freeze()} disabled={busy}
                  className="flex items-center gap-1 rounded-md border border-status-info-border px-2 py-1 text-status-info hover:bg-surface-hover disabled:opacity-40"
                  data-testid="brief-freeze"
                >
                  <FileLock2 className="h-3.5 w-3.5" aria-hidden />
                  {t('briefFreezeAction')}
                </button>
              </div>
              {releases.length > 0 ? (
                <ul className="space-y-1.5" data-testid="brief-release-list">
                  {releases.map((release) => (
                    <li key={release.release_id} className="flex flex-wrap items-center gap-1.5 rounded-md border border-edge-subtle px-2 py-1.5 text-meta">
                      <span className="font-mono text-ink">v{release.release_no}</span>
                      {release.draft ? (
                        <span className="rounded-full border border-status-warning-border px-1.5 text-status-warning">
                          {t('briefDraftBadge')}
                        </span>
                      ) : (
                        <span className="rounded-full border border-status-success-border px-1.5 text-status-success">
                          {t('briefPublishedBadge')}
                        </span>
                      )}
                      <span className="text-ink-muted">
                        {t('briefReleaseMeta', { rev: release.frozen_revision })}
                      </span>
                      <span className="flex-1" />
                      <button
                        type="button" onClick={() => void download(release.release_id)}
                        aria-label={t('briefDownloadAria')}
                        className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-status-info"
                      >
                        <Download className="h-3.5 w-3.5" aria-hidden />
                      </button>
                      {release.shared ? (
                        <button
                          type="button" onClick={() => void unshare(release.release_id)}
                          aria-label={t('briefUnshareAria')}
                          title={t('briefUnshareTitle')}
                          className="rounded-sm p-1 text-status-warning hover:bg-surface-hover"
                        >
                          <ShieldOff className="h-3.5 w-3.5" aria-hidden />
                        </button>
                      ) : (
                        <button
                          type="button" onClick={() => void share(release.release_id)}
                          aria-label={t('briefShareAria')}
                          title={t('briefShareTitle')}
                          className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-status-info"
                        >
                          <Share2 className="h-3.5 w-3.5" aria-hidden />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-meta text-ink-muted">{t('briefNoReleases')}</p>
              )}
            </section>
          </>
        ) : null}
      </div>

      {/* 证据侧栏（BRF 指令 2：带我看对应数据和分母） */}
      {evidence ? (
        <aside
          className="absolute inset-y-0 right-0 z-30 w-[320px] overflow-y-auto border-l border-edge-subtle bg-surface-raised p-3 shadow-lg"
          data-testid="brief-evidence-drawer"
          role="dialog"
          aria-label={t('evidenceDrawerAria')}
        >
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-caption font-medium uppercase tracking-wide text-status-info">
              {t('evidenceDrawerTitle')}
            </h3>
            <button
              type="button" aria-label={t('evidenceCloseAria')}
              onClick={() => setEvidence(null)}
              className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
            >
              <X size={13} aria-hidden />
            </button>
          </div>
          <EvidenceDetail inspection={evidence} />
        </aside>
      ) : null}
    </div>
  );
}

/** 单块渲染 + 行内操作。 */
function BlockRow(props: {
  block: BriefBlockDto;
  staleReason: string | null;
  editing: boolean;
  editDraft: string;
  facts: BriefResponseDto['spec']['facts'];
  busy: boolean;
  onEditStart: (block: BriefBlockDto) => void;
  onEditChange: (text: string) => void;
  onEditSave: (blockId: string) => void;
  onEditCancel: () => void;
  onToggleLock: (block: BriefBlockDto) => void;
  onDelete: (blockId: string) => void;
  onMove: (block: BriefBlockDto, dir: -1 | 1) => void;
  onInspect: (factId: string) => void;
}): React.ReactElement {
  const { block, staleReason, editing, editDraft, facts, busy } = props;
  const t = useT('story');
  const fact = facts.find((f) => f.fact_id === block.fact_id);

  return (
    <div>
      <div className="flex items-start gap-1.5">
        <div className="min-w-0 flex-1">
          {block.kind === 'metric' && fact ? (
            <button
              type="button"
              onClick={() => props.onInspect(fact.fact_id)}
              className="flex w-full flex-col items-start text-left hover:opacity-80"
              data-testid="brief-metric-card"
            >
              <span className="flex items-center gap-1.5">
                <span className="truncate text-ink-secondary">{fact.label}</span>
                <span className={`font-mono text-meta ${STATUS_KIND[fact.status]}`}>
                  [{t(fact.status === 'verified' ? 'factStatusVerified' : fact.status === 'warning' ? 'factStatusWarning' : 'factStatusUnverified')}]
                </span>
              </span>
              <span className="font-mono text-body text-ink">
                {fact.value}
                <span className="ml-1 text-meta text-ink-muted">{fact.unit}</span>
              </span>
              <span className="truncate text-meta text-ink-muted">
                {t('briefMetricSource', { source: fact.basis.source_label || fact.basis.artifact_ref || '?' })}
              </span>
            </button>
          ) : block.kind === 'map_view' ? (
            <div className="text-ink-secondary" data-testid="brief-mapview-summary">
              <p className="font-mono text-meta">
                zoom {block.map_view?.zoom ?? '?'} · [{block.map_view?.center?.join(', ') ?? '?'}]
              </p>
              <p className="text-meta text-ink-muted">
                {t('briefMapPinNote', {
                  fp: (block.map_view?.mapspec_fingerprint || '').slice(0, 12) || t('briefMapPinMissing'),
                  layers: block.map_view?.visible_layer_ids?.length ?? 0,
                })}
              </p>
            </div>
          ) : block.kind === 'chart' || block.kind === 'table' ? (
            <p className="truncate text-ink-secondary">
              {t(block.kind === 'chart' ? 'briefKindChart' : 'briefKindTable')} · <span className="font-mono text-meta">{block.widget_id}</span>
            </p>
          ) : block.kind === 'source' ? (
            <p className="truncate text-meta text-ink-muted">
              {t('briefSourceLine', {
                label: block.binding?.source_label ?? block.binding?.artifact_ref ?? '?',
                at: block.binding?.produced_at ?? '',
              })}
            </p>
          ) : editing ? (
            <div className="space-y-1">
              <textarea
                data-testid="brief-block-editor"
                data-autofocus
                value={editDraft}
                onChange={(e) => props.onEditChange(e.target.value)}
                rows={Math.min(10, Math.max(3, editDraft.split('\n').length + 1))}
                className="w-full rounded-sm border border-edge-subtle bg-surface-raised px-1.5 py-1 font-mono text-caption text-ink outline-none focus:border-status-info"
                aria-label={t('briefEditorAria')}
              />
              <div className="flex gap-1.5">
                <button
                  type="button" disabled={busy}
                  onClick={() => props.onEditSave(block.id)}
                  className="rounded-sm border border-status-info-border px-2 py-0.5 text-meta text-status-info hover:bg-surface-hover disabled:opacity-40"
                >
                  {t('briefEditorSave')}
                </button>
                <button
                  type="button" onClick={props.onEditCancel}
                  className="rounded-sm border border-edge-subtle px-2 py-0.5 text-meta text-ink-secondary hover:bg-surface-hover"
                >
                  {t('briefEditorCancel')}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => props.onEditStart(block)}
              disabled={block.locked}
              className="w-full whitespace-pre-wrap text-left font-mono text-caption leading-relaxed text-ink-secondary hover:bg-surface-hover disabled:hover:bg-transparent"
              data-testid="brief-text-block"
            >
              {(block.text || '').slice(0, 400) || t('briefEmptyText')}
            </button>
          )}
        </div>

        {/* 行内操作 */}
        <div className="flex shrink-0 items-center gap-0.5">
          {staleReason ? (
            <span
              className="rounded-full border border-status-warning-border px-1.5 text-meta text-status-warning"
              title={t('briefStaleReason', { reason: staleReason })}
              data-testid="brief-stale-badge"
            >
              {t('briefStaleBadge')}
            </span>
          ) : null}
          <button
            type="button"
            aria-label={block.locked ? t('briefUnlockAria') : t('briefLockAria')}
            onClick={() => props.onToggleLock(block)}
            className={`rounded-sm p-1 hover:bg-surface-hover ${block.locked ? 'text-status-warning' : 'text-ink-muted'}`}
          >
            {block.locked ? <Lock className="h-3 w-3" aria-hidden /> : <LockOpen className="h-3 w-3" aria-hidden />}
          </button>
          <button
            type="button" aria-label={t('briefMoveUpAria')}
            onClick={() => props.onMove(block, -1)}
            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover"
          >↑</button>
          <button
            type="button" aria-label={t('briefMoveDownAria')}
            onClick={() => props.onMove(block, 1)}
            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover"
          >↓</button>
          <button
            type="button" aria-label={t('briefDeleteAria')}
            onClick={() => props.onDelete(block.id)}
            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-status-error"
          >
            <Trash2 className="h-3 w-3" aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}

/** 证据详情（值/来源/口径/复算/依赖）。 */
function EvidenceDetail(props: { inspection: EvidenceInspectionDto }): React.ReactElement {
  const { inspection } = props;
  const t = useT('story');
  const fact = inspection.fact;
  const basis = fact.basis;
  return (
    <div className="space-y-2 text-caption">
      <p className="font-medium text-ink">{fact.label}</p>
      <p className="font-mono text-body text-ink">
        {fact.value} <span className="text-meta text-ink-muted">{fact.unit}</span>
        <span className={`ml-2 text-meta ${STATUS_KIND[fact.status]}`}>
          [{t(fact.status === 'verified' ? 'factStatusVerified' : fact.status === 'warning' ? 'factStatusWarning' : 'factStatusUnverified')}]
        </span>
      </p>
      {fact.status_reason ? (
        <p className="rounded-sm border border-status-warning-border bg-surface-sunken px-1.5 py-1 text-meta text-status-warning">
          {fact.status_reason}
        </p>
      ) : null}
      <dl className="space-y-0.5 text-meta text-ink-secondary">
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-ink-muted">{t('evidenceSource')}</dt>
          <dd className="truncate">{basis.source_label || basis.artifact_ref || '—'}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-ink-muted">{t('evidenceField')}</dt>
          <dd className="truncate font-mono">{basis.field_path || '—'}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-ink-muted">{t('evidenceAuthoritative')}</dt>
          <dd className="font-mono">
            {inspection.authoritative_value ?? '—'}
          </dd>
        </div>
        {basis.method ? (
          <div className="flex gap-1.5">
            <dt className="shrink-0 text-ink-muted">{t('evidenceMethod')}</dt>
            <dd className="truncate">{basis.method}</dd>
          </div>
        ) : null}
        {basis.temporal_window ? (
          <div className="flex gap-1.5">
            <dt className="shrink-0 text-ink-muted">{t('evidenceWindow')}</dt>
            <dd>{basis.temporal_window}</dd>
          </div>
        ) : null}
        {basis.produced_at ? (
          <div className="flex gap-1.5">
            <dt className="shrink-0 text-ink-muted">{t('evidenceProducedAt')}</dt>
            <dd>{basis.produced_at}</dd>
          </div>
        ) : null}
      </dl>
      {Object.keys(inspection.dependencies).length > 0 ? (
        <div>
          <p className="mb-0.5 text-meta text-ink-muted">{t('evidenceDeps')}</p>
          <ul className="space-y-0.5">
            {Object.entries(inspection.dependencies).map(([id, dep]) => (
              <li key={id} className="flex items-center gap-1.5 text-meta">
                <span className="truncate text-ink-secondary">{dep.label}</span>
                <span className="font-mono text-ink">{dep.value}{dep.unit}</span>
                <span className={`text-meta ${STATUS_KIND[dep.status]}`}>
                  [{t(dep.status === 'verified' ? 'factStatusVerified' : dep.status === 'warning' ? 'factStatusWarning' : 'factStatusUnverified')}]
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {inspection.artifact ? (
        <p className="text-meta text-ink-muted">
          {inspection.artifact.registered
            ? t('evidenceRegistered')
            : t('evidenceNotRegistered')}
        </p>
      ) : null}
    </div>
  );
}
