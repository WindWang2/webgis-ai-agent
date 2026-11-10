'use client';

/**
 * G17 staged 导入工作台 —— 多文件/多容器成员/解释计划/预览/提交/恢复。
 *
 * 合同：
 * - 状态唯一事实源在服务器（batch.json）：刷新页面后从这里恢复配置与收据，
 *   不依赖 React 内存（场景 16）。
 * - 迟到响应守卫：batch 切换代数（generation）递增，旧响应不得覆写当前
 *   批次（任务书 WP10）。
 * - 有界渲染：成员/字段/样本行显示数量封顶 + “另有 N 个”披露。
 * - 「清除未提交文件」与「移除数据集」是两个动作：清理只影响 staging。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { FileUp, Loader2, RefreshCw, X } from 'lucide-react';

import { useT } from '@/lib/i18n/useT';
import {
  createImportBatch,
  commitImportBatch,
  deleteImportBatch,
  getImportBatch,
  getImportReceipts,
  listImportBatches,
  previewImportMembers,
  saveImportPlan,
  type ImportBatch,
  type ImportBatchSummary,
  type ImportMember,
  type ImportReceipt,
  type MemberConfig,
  type PreviewRow,
} from '@/lib/api/data-import';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { InlineNotice } from '@/components/shared/inline-notice';

const MEMBER_RENDER_CAP = 50;
const FIELD_RENDER_CAP = 30;
const SAMPLE_ROW_CAP = 10;
const SAMPLE_COL_CAP = 8;
const REJECT_RENDER_CAP = 10;

type Draft = MemberConfig & { dirty?: boolean };

function draftFromServer(saved: Record<string, unknown> | undefined): Draft {
  if (!saved) return { mode: 'auto' };
  return { ...(saved as MemberConfig), dirty: false };
}

export function ImportWorkbench({ onClose }: { onClose: () => void }) {
  const t = useT('dataImport');
  const { sessionId, ownerToken } = getSessionIdentity();

  const [batches, setBatches] = useState<ImportBatchSummary[] | null>(null);
  const [active, setActive] = useState<ImportBatch | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewRow[] | null>(null);
  const [receipts, setReceipts] = useState<Record<string, ImportReceipt> | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // creating|plan|preview|commit|cancel
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const genRef = useRef(0);

  const refreshList = useCallback(async () => {
    if (!sessionId) return;
    try {
      setBatches((await listImportBatches(sessionId, { ownerToken })) ?? []);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  }, [sessionId, ownerToken]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const loadBatch = useCallback(
    async (batchId: string) => {
      if (!sessionId) return;
      const gen = ++genRef.current;
      setBusy('load');
      setError(null);
      try {
        const detail = await getImportBatch(batchId, sessionId, { ownerToken });
        if (genRef.current !== gen) return; // 迟到响应丢弃
        setActive(detail);
        const next: Record<string, Draft> = {};
        for (const m of detail.members ?? []) {
          next[m.member_key] = draftFromServer(detail.interpretations?.[m.member_key]);
        }
        setDrafts(next);
        setFocusKey((k) => k ?? detail.members?.[0]?.member_key ?? null);
        setPreview(null);
        setReceipts(
          detail.receipts && Object.keys(detail.receipts).length
            ? detail.receipts
            : null
        );
      } catch (e) {
        setError(String((e as Error).message ?? e));
      } finally {
        setBusy(null);
      }
    },
    [sessionId, ownerToken]
  );

  const poll = useBoundedPoll<{ status: string; receipts: Record<string, ImportReceipt> }>({
    enabled: active?.status === 'committing' && busy === null,
    resetKey: active?.batch_id ?? '',
    pollIntervalMs: 1500,
    fetcher: async (signal: AbortSignal) => {
      if (!sessionId || !active) {
        return { status: 'unknown', receipts: {} };
      }
      return getImportReceipts(active.batch_id, sessionId, { signal, ownerToken });
    },
  });

  useEffect(() => {
    if (poll.data && active) {
      setReceipts(poll.data.receipts);
      if (poll.data.status !== 'committing') {
        setActive((prev) =>
          prev && prev.batch_id === active.batch_id
            ? { ...prev, status: poll.data!.status }
            : prev
        );
        void loadBatch(active.batch_id);
        void refreshList();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poll.data]);

  const members = active?.members ?? [];
  const selectedKeys = useMemo(
    () => members.filter((m) => m.selected && !m.unsupported_reason).map((m) => m.member_key),
    [members]
  );
  const focusMember = members.find((m) => m.member_key === focusKey) ?? null;
  const shownMembers = members.slice(0, MEMBER_RENDER_CAP);

  const handleCreate = async (files: FileList | null) => {
    if (!files || !files.length || !sessionId) return;
    setBusy('creating');
    setError(null);
    setPct(0);
    try {
      const created = await createImportBatch(Array.from(files), sessionId, setPct, { ownerToken });
      await refreshList();
      await loadBatch(created.batch_id);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const patchDraft = (key: string, patch: Partial<Draft>) => {
    setDrafts((prev) => ({ ...prev, [key]: { ...prev[key], ...patch, dirty: true } }));
  };

  const handleSavePlan = async () => {
    if (!sessionId || !active) return;
    setBusy('plan');
    setError(null);
    try {
      const configs: Record<string, MemberConfig> = {};
      for (const m of members) {
        const d = drafts[m.member_key];
        if (!m.selected || m.unsupported_reason || !d) continue;
        const c: MemberConfig = { mode: d.mode ?? 'auto' };
        if (d.x_column) c.x_column = d.x_column;
        if (d.y_column) c.y_column = d.y_column;
        if (d.wkt_column) c.wkt_column = d.wkt_column;
        if (d.encoding) c.encoding = d.encoding;
        if (d.delimiter) c.delimiter = d.delimiter;
        if (d.source_crs_declared) c.source_crs_declared = d.source_crs_declared;
        if (d.field_types && Object.keys(d.field_types).length) c.field_types = d.field_types;
        configs[m.member_key] = c;
      }
      await saveImportPlan(
        active.batch_id,
        sessionId,
        { member_configs: configs, selected: selectedKeys, expected_plan_version: active.plan_version ?? null },
        { ownerToken }
      );
      await loadBatch(active.batch_id); // 保存后配置回到服务器事实源
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const handlePreview = async () => {
    if (!sessionId || !active || !selectedKeys.length) return;
    setBusy('preview');
    setError(null);
    try {
      const out = await previewImportMembers(
        active.batch_id,
        sessionId,
        { member_keys: selectedKeys, row_limit: 200 },
        { ownerToken }
      );
      setPreview(out.previews);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const handleCommit = async () => {
    if (!sessionId || !active) return;
    setBusy('commit');
    setError(null);
    try {
      // 先落计划（确保提交用的解释与预览一致），再提交
      const configs: Record<string, MemberConfig> = {};
      for (const m of members) {
        const d = drafts[m.member_key];
        if (!m.selected || m.unsupported_reason || !d) continue;
        configs[m.member_key] = d as MemberConfig;
      }
      await saveImportPlan(
        active.batch_id,
        sessionId,
        { member_configs: configs, selected: selectedKeys },
        { ownerToken }
      );
      await commitImportBatch(active.batch_id, sessionId, {}, { ownerToken });
      await loadBatch(active.batch_id);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const handleCancelCommit = async () => {
    if (!sessionId || !active) return;
    setBusy('cancel');
    try {
      const { cancelImportBatch } = await import('@/lib/api/data-import');
      await cancelImportBatch(active.batch_id, sessionId, { ownerToken });
      await loadBatch(active.batch_id);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const handleCleanup = async () => {
    if (!sessionId || !active) return;
    setBusy('cleanup');
    try {
      await deleteImportBatch(active.batch_id, sessionId, { ownerToken });
      setActive(null);
      setPreview(null);
      setReceipts(null);
      await refreshList();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const focusDraft = focusMember ? (drafts[focusMember.member_key] ?? { mode: 'auto' as const }) : null;

  return (
    <section
      aria-label={t('title')}
      className="flex max-h-[80vh] flex-col gap-3 overflow-y-auto rounded-md border bg-popover p-3 text-sm"
      data-testid="import-workbench"
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold">{t('title')}</h3>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="rounded p-1 hover:bg-accent"
            onClick={() => void refreshList()}
            aria-label={t('refresh')}
          >
            <RefreshCw size={14} />
          </button>
          <button
            type="button"
            className="rounded p-1 hover:bg-accent"
            onClick={onClose}
            aria-label={t('close')}
            data-testid="import-workbench-close"
          >
            <X size={14} />
          </button>
        </div>
      </header>

      {!sessionId && (
        <InlineNotice variant="warning">{t('needSession')}</InlineNotice>
      )}

      {error && (
        <p role="alert" className="text-destructive" data-testid="import-workbench-error">
          {t('error', { p0: error })}
        </p>
      )}

      {/* 新建批次 */}
      <div className="rounded border p-2">
        <div className="flex items-center gap-2">
          <label className="inline-flex cursor-pointer items-center gap-1 rounded border px-2 py-1 hover:bg-accent">
            <FileUp size={14} aria-hidden />
            <span>{t('pickFiles')}</span>
            <input
              type="file"
              multiple
              className="hidden"
              accept=".geojson,.json,.gpkg,.kml,.zip,.csv,.xlsx"
              onChange={(e) => {
                void handleCreate(e.target.files);
                e.target.value = '';
              }}
              disabled={!sessionId || busy === 'creating'}
              aria-label={t('newBatch')}
              data-testid="import-files-input"
            />
          </label>
          {busy === 'creating' && (
            <span role="status" className="inline-flex items-center gap-1 text-muted-foreground">
              <Loader2 size={12} className="animate-spin" />
              {t('creating', { p0: String(pct) })}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{t('newBatchHint')}</p>
      </div>

      {/* 批次列表 */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="font-medium">{t('batchList')}</span>
          <span className="text-xs text-muted-foreground">{t('restoreHint')}</span>
        </div>
        {batches !== null && batches.length === 0 && (
          <p className="text-xs text-muted-foreground">{t('noBatches')}</p>
        )}
        <ul className="space-y-1" data-testid="import-batch-list">
          {(batches ?? []).slice(0, 10).map((b) => (
            <li key={b.batch_id}>
              <button
                type="button"
                className={`w-full rounded border px-2 py-1 text-left text-xs ${
                  active?.batch_id === b.batch_id ? 'border-primary bg-accent' : 'hover:bg-accent'
                }`}
                onClick={() => void loadBatch(b.batch_id)}
                aria-pressed={active?.batch_id === b.batch_id}
              >
                <span className="font-mono">{b.batch_id.slice(0, 11)}</span>{' '}
                · {t('batchStatus', { p0: b.status })} · {b.member_count} members
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* 成员清单 */}
      {active && (
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="font-medium">{t('members')}</span>
            <span className="text-xs text-muted-foreground">
              {t('planVersion', { p0: String(active.plan_version ?? 0) })}
            </span>
          </div>
          <table className="w-full table-fixed text-xs" aria-label={t('members')}>
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="w-6" scope="col" />
                <th scope="col">{t('columnMember')}</th>
                <th className="w-20" scope="col">{t('columnType')}</th>
                <th className="w-16" scope="col">{t('columnCount')}</th>
                <th className="w-32" scope="col">{t('columnCrs')}</th>
                <th className="w-20" scope="col">{t('columnStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {shownMembers.map((m) => (
                <MemberRow
                  key={m.member_key}
                  member={m}
                  focused={m.member_key === focusKey}
                  onFocus={() => setFocusKey(m.member_key)}
                  t={t}
                />
              ))}
            </tbody>
          </table>
          {members.length > MEMBER_RENDER_CAP && (
            <p className="mt-1 text-xs text-muted-foreground">
              {t('moreMembers', { p0: String(members.length - MEMBER_RENDER_CAP) })}
            </p>
          )}
        </div>
      )}

      {/* 解释配置 */}
      {focusMember && focusDraft && (
        <div className="rounded border p-2" data-testid="import-config-panel">
          <p className="mb-2 font-medium">
            {t('config')} — {focusMember.display_name}
          </p>
          {focusMember.kind === 'table' ? (
            <div className="grid grid-cols-2 gap-2 text-xs">
              <label>
                {t('mode')}
                <select
                  className="mt-0.5 w-full rounded border bg-background px-1 py-0.5"
                  value={focusDraft.mode ?? 'auto'}
                  onChange={(e) => patchDraft(focusMember.member_key, { mode: e.target.value as MemberConfig['mode'] })}
                >
                  <option value="auto">{t('modeAuto')}</option>
                  <option value="points">{t('modePoints')}</option>
                  <option value="wkt">{t('modeWkt')}</option>
                  <option value="table">{t('modeTable')}</option>
                </select>
              </label>
              <label>
                {t('encoding')}
                <input
                  className="mt-0.5 w-full rounded border bg-background px-1 py-0.5"
                  placeholder="utf-8 / gb18030"
                  value={focusDraft.encoding ?? ''}
                  onChange={(e) => patchDraft(focusMember.member_key, { encoding: e.target.value || null })}
                />
              </label>
              <label>
                {t('delimiter')}
                <input
                  className="mt-0.5 w-full rounded border bg-background px-1 py-0.5"
                  placeholder=", ; \t"
                  maxLength={3}
                  value={focusDraft.delimiter ?? ''}
                  onChange={(e) => patchDraft(focusMember.member_key, { delimiter: e.target.value || null })}
                />
              </label>
              <label>
                {t('sourceCrs')}
                <input
                  className="mt-0.5 w-full rounded border bg-background px-1 py-0.5"
                  placeholder="EPSG:32650"
                  value={focusDraft.source_crs_declared ?? ''}
                  onChange={(e) =>
                    patchDraft(focusMember.member_key, { source_crs_declared: e.target.value || null })
                  }
                />
              </label>
              {(focusDraft.mode ?? 'auto') !== 'table' && (
                <>
                  <ColumnSelect
                    label={t('xColumn')}
                    fields={focusMember.fields}
                    value={focusDraft.x_column ?? (focusMember.hints?.x_column as string) ?? ''}
                    onChange={(v) => patchDraft(focusMember.member_key, { x_column: v || null })}
                  />
                  <ColumnSelect
                    label={t('yColumn')}
                    fields={focusMember.fields}
                    value={focusDraft.y_column ?? (focusMember.hints?.y_column as string) ?? ''}
                    onChange={(v) => patchDraft(focusMember.member_key, { y_column: v || null })}
                  />
                </>
              )}
              {(focusDraft.mode ?? 'auto') === 'wkt' && (
                <ColumnSelect
                  label={t('wktColumn')}
                  fields={focusMember.fields}
                  value={focusDraft.wkt_column ?? ''}
                  onChange={(v) => patchDraft(focusMember.member_key, { wkt_column: v || null })}
                />
              )}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2 text-xs">
              <label>
                {t('sourceCrs')}
                <input
                  className="mt-0.5 w-full rounded border bg-background px-1 py-0.5"
                  placeholder={focusMember.crs ?? 'EPSG:…'}
                  disabled={Boolean(focusMember.crs)}
                  value={
                    focusMember.crs
                      ? focusMember.crs
                      : (focusDraft.source_crs_declared ?? '')
                  }
                  onChange={(e) =>
                    patchDraft(focusMember.member_key, { source_crs_declared: e.target.value || null })
                  }
                />
              </label>
            </div>
          )}
          {focusMember.kind === 'table' && focusMember.fields.length > 0 && (
            <div className="mt-2">
              <p className="text-xs text-muted-foreground">{t('fieldTypes')}</p>
              <div className="mt-1 grid max-h-32 grid-cols-2 gap-1 overflow-y-auto text-xs">
                {focusMember.fields.slice(0, FIELD_RENDER_CAP).map((f) => (
                  <label key={f} className="flex items-center gap-1">
                    <span className="flex-1 truncate" title={f}>{f}</span>
                    <select
                      className="rounded border bg-background"
                      value={focusDraft.field_types?.[f] ?? 'text'}
                      onChange={(e) =>
                        patchDraft(focusMember.member_key, {
                          field_types: {
                            ...(focusDraft.field_types ?? {}),
                            [f]: e.target.value,
                          },
                        })
                      }
                      aria-label={f}
                    >
                      <option value="text">{t('fieldTypeText')}</option>
                      <option value="number">{t('fieldTypeNumber')}</option>
                      <option value="boolean">{t('fieldTypeBoolean')}</option>
                      <option value="date">{t('fieldTypeDate')}</option>
                    </select>
                  </label>
                ))}
              </div>
              {focusMember.fields.length > FIELD_RENDER_CAP && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {t('moreFields', { p0: String(focusMember.fields.length - FIELD_RENDER_CAP) })}
                </p>
              )}
            </div>
          )}
          <p className="mt-2 text-xs text-muted-foreground">{t('sourceCrsHint')}</p>
        </div>
      )}

      {/* 操作区 */}
      {active && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
            onClick={() => void handleSavePlan()}
            disabled={!selectedKeys.length || busy !== null}
          >
            {busy === 'plan' ? t('saving') : t('savePlan')}
          </button>
          <button
            type="button"
            className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
            onClick={() => void handlePreview()}
            disabled={!selectedKeys.length || busy !== null}
            data-testid="import-preview-btn"
          >
            {busy === 'preview' ? t('previewing') : t('preview')}
          </button>
          {active.status === 'committing' ? (
            <button
              type="button"
              className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
              onClick={() => void handleCancelCommit()}
              disabled={busy !== null}
              data-testid="import-cancel-btn"
            >
              {busy === 'cancel' ? t('cancelling') : t('cancel')}
            </button>
          ) : (
            <ConfirmAction
              label={busy === 'commit' ? t('committing') : t('commit')}
              confirmLabel={t('confirmCommit', { p0: String(selectedKeys.length) })}
              disabled={!selectedKeys.length || busy !== null || active.status === 'committing'}
              onConfirm={() => void handleCommit()}
              data-testid="import-commit-btn"
            />
          )}
          <span className="text-xs text-muted-foreground">
            {t('batchStatus', { p0: active.status })}
          </span>
        </div>
      )}

      {/* 预览 */}
      {preview !== null && (
        <div className="rounded border p-2" data-testid="import-preview-panel">
          {preview.map((p) => (
            <PreviewBlock key={p.member_key} p={p} t={t} />
          ))}
        </div>
      )}

      {/* 收据 */}
      {receipts !== null && (
        <div data-testid="import-receipts">
          <p className="mb-1 font-medium">{t('receipts')}</p>
          <ul className="space-y-1 text-xs">
            {Object.entries(receipts).map(([key, r]) => (
              <li key={key} className="rounded border p-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px] text-muted-foreground">{key}</span>
                  <span
                    data-status={r.status}
                    className={
                      r.status === 'completed'
                        ? 'text-green-600'
                        : r.status === 'failed'
                          ? 'text-destructive'
                          : 'text-muted-foreground'
                    }
                  >
                    {r.status === 'completed'
                      ? t('receiptCompleted')
                      : r.status === 'failed'
                        ? t('receiptFailed')
                        : t('receiptCancelled')}
                    {r.duplicate ? ` · ${t('receiptDuplicate')}` : ''}
                  </span>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                  {r.output_rows != null && (
                    <span>{t('receiptRows', { p0: String(r.output_rows), p1: String(r.rejected_rows ?? 0) })}</span>
                  )}
                  {r.output_ref && (
                    <span className="font-mono" data-testid="receipt-ref">{r.output_ref}</span>
                  )}
                  {r.error_code && (
                    <span className="text-destructive">
                      {t('receiptError', { p0: r.error_code, p1: r.error_message ?? '' })}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 披露 + 清理 */}
      {active && active.notes && active.notes.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">{t('notes')}</summary>
          <ul className="mt-1 list-inside list-disc text-muted-foreground">
            {active.notes.slice(-10).map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </details>
      )}
      {active && (
        <div className="mt-auto">
          <ConfirmAction
            label={t('cleanup')}
            confirmLabel={t('cleanupWarning')}
            disabled={busy !== null || active.status === 'committing'}
            onConfirm={() => void handleCleanup()}
            data-testid="import-cleanup-btn"
          />
        </div>
      )}
    </section>
  );
}

function MemberRow({
  member,
  focused,
  onFocus,
  t,
}: {
  member: ImportMember;
  focused: boolean;
  onFocus: () => void;
  t: ReturnType<typeof useT>;
}) {
  const disabled = Boolean(member.unsupported_reason);
  return (
    <tr
      className={`border-b last:border-0 ${focused ? 'bg-accent' : ''}`}
      data-testid={`member-row-${member.member_key}`}
      onClick={onFocus}
    >
      <td>
        <input
          type="checkbox"
          checked={member.selected && !disabled}
          disabled={disabled}
          readOnly
          aria-label={member.display_name}
        />
      </td>
      <td className="truncate px-1" title={member.display_name}>
        <button type="button" className="text-left underline-offset-2 hover:underline" onClick={onFocus}>
          {member.display_name}
        </button>
        {member.unsupported_reason && (
          <span className="ml-1 text-[11px] text-destructive">
            {t('unsupported', { p0: member.unsupported_reason })}
          </span>
        )}
      </td>
      <td>{member.kind}</td>
      <td>
        {member.feature_count == null ? t('countUnknown') : member.feature_count}
      </td>
      <td className="truncate px-1 text-[11px]" title={member.crs ?? ''}>
        {member.crs ?? '—'}
        {member.crs_source === 'unknown' && member.crs == null && ' (?)'}
      </td>
      <td data-status={member.status}>{member.status}</td>
    </tr>
  );
}

function ColumnSelect({
  label,
  fields,
  value,
  onChange,
}: {
  label: string;
  fields: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label>
      {label}
      <select
        className="mt-0.5 w-full rounded border bg-background px-1 py-0.5"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        {fields.map((f) => (
          <option key={f} value={f}>{f}</option>
        ))}
      </select>
    </label>
  );
}

function PreviewBlock({ p, t }: { p: PreviewRow; t: ReturnType<typeof useT> }) {
  if (p.error) {
    return (
      <div className="mb-2 border-b pb-2 last:mb-0">
        <p className="text-destructive">
          {t('previewError', { p0: p.error.code, p1: p.error.message })}
        </p>
      </div>
    );
  }
  const rows = (p.sample_rows ?? []).slice(0, SAMPLE_ROW_CAP);
  const cols = (p.columns ?? []).slice(0, SAMPLE_COL_CAP);
  const rejects = (p.rejects_inline ?? []).slice(0, REJECT_RENDER_CAP);
  return (
    <div className="mb-3 border-b pb-2 last:mb-0" data-testid={`preview-${p.member_key}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">{p.member_key}</span>
        {p.effective_mode && <span className="rounded bg-muted px-1">{p.effective_mode}</span>}
        {p.crs?.confirmed ? (
          <span className="text-green-600">
            {t('crsConfirmed', { p0: p.crs.crs ?? '', p1: p.crs.crs_source })}
          </span>
        ) : (
          <span className="text-amber-600">{t('crsUnknown')}</span>
        )}
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="text-[11px]">
            <thead>
              <tr className="text-left text-muted-foreground">
                {cols.map((c) => (
                  <th key={c} className="px-1 whitespace-nowrap">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <td key={c} className="px-1 whitespace-nowrap">{String(r[c] ?? '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rejects.length > 0 && (
        <div className="mt-1">
          <p className="text-xs text-destructive">
            {t('previewRejects')} · {p.rejected_rows}
          </p>
          <ul className="list-inside list-disc text-[11px] text-muted-foreground">
            {rejects.map((r, i) => (
              <li key={i}>
                #{r.row} {r.reason}
                {r.column ? ` (${r.column})` : ''} {r.raw ?? ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.sampling_disclosure && (
        <p className="mt-1 text-[11px] text-muted-foreground">{p.sampling_disclosure}</p>
      )}
    </div>
  );
}
