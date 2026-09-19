'use client';


/**
 * SnapshotTimeline — Workspace 快照时间线（ADR-0143 P4）。
 *
 * 后端契约（勘察报告 §2.5）：
 * - 列表硬上限 50（非 Page 信封）；created_at 为可选 epoch 秒；
 * - inspect 返回 SnapshotVerification（liveness + integrity map）；
 * - restore 同步（verify=仅核查 / register=重绑账本+重物化），无 job 句柄 →
 *   进度以 busy 态呈现，死 ref 如实降级为 expired/degraded 披露；
 * - 无快照 diff 端点 → 「生成对比」在前端聚合两份 verify 报告（hook 内）。
 * 除列表外全部要求登录 + 会话上下文（session_id 必填）。
 */

import { useState } from 'react';
import {
  Camera,
  RefreshCw,
  ShieldCheck,
  Undo2,
  GitCompare,
  Copy,
  Lock,
} from 'lucide-react';

import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { SField } from '@/components/shared/section-title';
import { useToastStore } from '@/components/ui/toast';
import { useProjectSnapshots } from '@/lib/hooks/use-project-assets';
import type { SnapshotVerification, WorkspaceSnapshotSummary } from '@/lib/api/project-assets';
import { shortId } from '@/lib/workflow/recovery';
import { formatEpoch } from './format';
import { useT } from '@/lib/i18n/useT';

export interface SnapshotTimelineProps {
  projectId: string;
  sessionId?: string | null;
  authed: boolean;
}

function VerificationSummary({ v }: { v: SnapshotVerification }) {
  const t = useT('project');
  return (
    <div className="space-y-1 rounded-sm bg-surface-sunken px-2 py-1.5 text-micro">
      <p className="flex flex-wrap items-center gap-x-2">
        <span className={v.restorable ? 'text-status-success' : 'text-status-critical'}>
          {v.restorable ? '可恢复' : '存在缺失'}
        </span>
        <span className="text-ink-muted">
          {t('p0P1P2P3Mapspec', { p0: v.artifacts.live, p1: v.artifacts.total, p2: v.layers.live, p3: v.layers.total, p4: v.mapspec_available ? '可用' : '无' })}</span>
      </p>
      {(v.artifacts.missing.length > 0 || v.layers.missing.length > 0) && (
        <ul className="space-y-0.5 text-status-critical">
          {v.artifacts.missing.slice(0, 3).map((m) => (
            <li key={`a-${m}`} className="truncate">{t('k15e09oz', { p0: shortId(m, 20) })}</li>
          ))}
          {v.layers.missing.slice(0, 3).map((m) => (
            <li key={`l-${m}`} className="truncate">{t('k1r5vh9f', { p0: shortId(m, 20) })}</li>
          ))}
        </ul>
      )}
      {Object.entries(v.integrity).some(([, s]) => s !== 'verified') && (
        <p className="text-status-warning">
          {t('p0P1', { p0: ' ', p1: Object.entries(v.integrity)
            .filter(([, s]) => s !== 'verified')
            .slice(0, 3)
            .map(([id, s]) => `${shortId(id, 8)}=${s}`)
            .join('、') })}</p>
      )}
    </div>
  );
}

export function SnapshotTimeline({ projectId, sessionId, authed }: SnapshotTimelineProps) {
  const t = useT('project');
  const sn = useProjectSnapshots(projectId, sessionId);
  const addToast = useToastStore((s) => s.addToast);
  const [showSave, setShowSave] = useState(false);
  const [label, setLabel] = useState('');
  const [materialize, setMaterialize] = useState<'none' | 'claimed' | 'all'>('none');
  const [inspectOpenId, setInspectOpenId] = useState('');
  const [restoreTarget, setRestoreTarget] = useState<WorkspaceSnapshotSummary | null>(null);
  const [restoreMode, setRestoreMode] = useState<'verify' | 'register'>('verify');
  const [cloneTarget, setCloneTarget] = useState<WorkspaceSnapshotSummary | null>(null);
  const [cloneTargetSession, setCloneTargetSession] = useState('');
  const [diffA, setDiffA] = useState('');
  const [diffB, setDiffB] = useState('');

  const canAct = authed && sn.sessionReady;

  const handleClone = async () => {
    if (!cloneTarget) return;
    const ok = await sn.clone(cloneTarget.snapshot_id, cloneTargetSession);
    if (ok) {
      addToast(`快照已克隆到会话 ${shortId(cloneTargetSession, 12)}`, 'success');
      setCloneTarget(null);
    }
  };

  const handleSave = async () => {
    const saved = await sn.save({ label: label.trim() || undefined, materialize });
    if (saved) {
      addToast(`快照已保存（${saved.snapshot_id}）`, 'success');
      setLabel('');
      setShowSave(false);
    }
  };

  const handleRestore = async () => {
    if (!restoreTarget) return;
    const result = await sn.restore(restoreTarget.snapshot_id, restoreMode);
    if (result) {
      if (result.error) {
        addToast(`恢复未执行：${result.error}`, 'error');
      } else if (restoreMode === 'verify') {
        addToast('核查完成——未写入任何状态', 'success');
      } else {
        addToast(`快照 ${shortId(restoreTarget.snapshot_id, 8)} 已恢复`, 'success');
      }
      setRestoreTarget(null);
    }
  };

  const handleDiff = async () => {
    if (!diffA || !diffB || diffA === diffB) {
      addToast('请选择两个不同的快照进行对比', 'error');
      return;
    }
    const result = await sn.loadDiff(diffA, diffB);
    if (result) addToast('对比完成', 'success');
  };

  const sorted = [...sn.snapshots].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));

  return (
    <section aria-labelledby="snap-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 id="snap-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
          <Camera size={14} className="text-ink-muted" aria-hidden /> {t('p0P12', { p0: sn.count, p1: sn.count >= sn.bounded ? `· 上限 ${sn.bounded}` : '' })}</h3>
        <span className="flex gap-1">
          <IconButton
            label={t('kczp5ix')}
            icon={RefreshCw}
            iconSize={13}
            disabled={sn.loading}
            onClick={() => {
              void sn.reload({ forceRefresh: true });
            }}
          />
          <IconButton
            label={t('kcl3nif')}
            icon={Camera}
            iconSize={14}
            active={showSave}
            disabled={!canAct}
            title={canAct ? undefined : authed ? '需要会话上下文' : '需要登录账号'}
            onClick={() => setShowSave(!showSave)}
          />
        </span>
      </div>

      {!authed && (
        <p className="flex items-center gap-1.5 text-caption text-ink-muted">
          <Lock size={12} aria-hidden /> {t('kuhjcy1')}</p>
      )}
      {authed && !sn.sessionReady && (
        <p className="text-caption text-ink-muted">{t('sessionId')}</p>
      )}

      {sn.error && <InlineNotice variant="error">{sn.error}</InlineNotice>}

      {showSave && canAct && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
          <SField label={t('k1utlzny')} value={label} onChange={setLabel} placeholder={t('k1qpnsri')} />
          <label className="block space-y-1">
            <span className="block text-meta font-medium text-ink-secondary">{t('kht3ffe')}</span>
            <select
              value={materialize}
              onChange={(e) => setMaterialize(e.target.value as typeof materialize)}
              className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
            >
              <option value="none">{t('none')}</option>
              <option value="claimed">{t('claimed')}</option>
              <option value="all">{t('all')}</option>
            </select>
          </label>
          <ConfirmAction
            label={t('keymt')}
            confirmLabel={materialize === 'all' ? '全量物化可能耗时，确认？' : '确认保存？'}
            onConfirm={() => {
              void handleSave();
            }}
            disabled={sn.busyId === 'save'}
          />
        </div>
      )}

      {sn.loading && sn.snapshots.length === 0 ? (
        <LoadingState label={t('kayhc7p')} />
      ) : sorted.length === 0 ? (
        <EmptyState icon={Camera} title={t('kg2vqvo')} description={t('k1ovkyc2')} />
      ) : (
        <>
          <ol className="relative space-y-1.5 border-l border-edge-subtle pl-3">
            {sorted.map((s) => {
              const v = sn.verifications[s.snapshot_id];
              const restoring = sn.busyId === s.snapshot_id;
              return (
                <li key={s.snapshot_id} className="relative">
                  <span
                    aria-hidden
                    className="absolute -left-[17px] top-2.5 h-2 w-2 rounded-full border border-edge-subtle bg-surface-raised"
                  />
                  <div className="rounded-md border border-edge-subtle bg-surface-raised px-panel py-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-meta font-medium text-ink">
                          {s.label || <span className="text-ink-muted">{t('khxlaeu')}</span>}
                        </div>
                        <div className="text-micro text-ink-muted">
                          {s.created_at != null ? formatEpoch(s.created_at) : '未知时间'} {t('p0P1P2P3', { p0: s.artifacts, p1: ' ', p2: s.layers, p3: s.home === 'project' ? '项目域' : '会话域' })}</div>
                      </div>
                      <span className="flex shrink-0 items-center gap-1">
                        <button
                          type="button"
                          aria-label={`核查快照 ${s.label || s.snapshot_id}`}
                          title={t('k196p42q')}
                          onClick={() => {
                            setInspectOpenId(inspectOpenId === s.snapshot_id ? '' : s.snapshot_id);
                            if (inspectOpenId !== s.snapshot_id || !v) void sn.inspect(s.snapshot_id);
                          }}
                          className="rounded-sm p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink"
                        >
                          <ShieldCheck size={13} aria-hidden />
                        </button>
                        <button
                          type="button"
                          aria-label={`克隆快照 ${s.label || s.snapshot_id}`}
                          title={t('k1nsl7by')}
                          disabled={!canAct || restoring}
                          onClick={() => {
                            setCloneTarget(s);
                            setCloneTargetSession('');
                          }}
                          className="rounded-sm p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink disabled:opacity-50"
                        >
                          <Copy size={13} aria-hidden />
                        </button>
                        <button
                          type="button"
                          aria-label={`恢复快照 ${s.label || s.snapshot_id}`}
                          title={t('khxv3')}
                          disabled={!canAct || restoring}
                          onClick={() => {
                            setRestoreTarget(s);
                            setRestoreMode('verify');
                          }}
                          className="rounded-sm p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink disabled:opacity-50"
                        >
                          <Undo2 size={13} aria-hidden />
                        </button>
                        <ConfirmAction
                          label={t('kfp1g')}
                          confirmLabel={t('k1m2fc1h')}
                          onConfirm={() => {
                            void sn.remove(s.snapshot_id).then((ok) => {
                              if (ok) addToast(`快照 ${shortId(s.snapshot_id, 8)} 已删除`, 'success');
                            });
                          }}
                          disabled={!canAct || restoring}
                          title={canAct ? '删除快照' : authed ? '需要会话上下文' : '需要登录账号'}
                        />
                      </span>
                    </div>
                    <div className="mt-0.5 font-mono text-micro text-ink-muted">{shortId(s.snapshot_id, 14)}</div>
                    {inspectOpenId === s.snapshot_id &&
                      (v ? (
                        <div className="mt-1.5">
                          <VerificationSummary v={v} />
                        </div>
                      ) : (
                        <LoadingState label={t('kgchiz4')} />
                      ))}
                    {restoring && <LoadingState label={t('k4qh7zg')} />}
                  </div>
                </li>
              );
            })}
          </ol>

          <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-sunken px-panel py-2">
            <p className="flex items-center gap-1.5 text-meta font-medium text-ink-secondary">
              <GitCompare size={13} aria-hidden /> {t('kf4ogm7')}</p>
            <div className="grid grid-cols-2 gap-1.5">
              <select
                aria-label={t('kpahdlr')}
                value={diffA}
                onChange={(e) => setDiffA(e.target.value)}
                className="rounded-sm border border-edge-subtle bg-surface-raised px-1.5 py-1 text-micro text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
              >
                <option value="">{t('kf0o4m')}</option>
                {sorted.map((s) => (
                  <option key={s.snapshot_id} value={s.snapshot_id}>
                    {s.label || shortId(s.snapshot_id, 8)}
                  </option>
                ))}
              </select>
              <select
                aria-label={t('ku3pul0')}
                value={diffB}
                onChange={(e) => setDiffB(e.target.value)}
                className="rounded-sm border border-edge-subtle bg-surface-raised px-1.5 py-1 text-micro text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
              >
                <option value="">{t('kk9njv')}</option>
                {sorted.map((s) => (
                  <option key={s.snapshot_id} value={s.snapshot_id}>
                    {s.label || shortId(s.snapshot_id, 8)}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => {
                void handleDiff();
              }}
              disabled={!canAct || sn.diffLoading || !diffA || !diffB}
              className="w-full rounded-sm border border-edge-subtle bg-surface-raised py-1 text-micro text-ink hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-60"
            >
              {sn.diffLoading ? '对比中…' : '生成对比'}
            </button>

            {sn.diff && (
              <div className="space-y-1.5 pt-1 text-micro">
                <div className="grid grid-cols-3 gap-1.5">
                  <div className="rounded-sm bg-surface-raised px-1.5 py-1">
                    <div className="text-ink-muted">{t('kchsr6s')}</div>
                    <div className={sn.diff.artifacts.totalDelta === 0 ? 'text-ink' : 'text-status-warning'}>
                      {sn.diff.artifacts.totalDelta >= 0 ? '+' : ''}
                      {sn.diff.artifacts.totalDelta}
                    </div>
                  </div>
                  <div className="rounded-sm bg-surface-raised px-1.5 py-1">
                    <div className="text-ink-muted">{t('kdnsvqs')}</div>
                    <div className={sn.diff.layers.totalDelta === 0 ? 'text-ink' : 'text-status-warning'}>
                      {sn.diff.layers.totalDelta >= 0 ? '+' : ''}
                      {sn.diff.layers.totalDelta}
                    </div>
                  </div>
                  <div className="rounded-sm bg-surface-raised px-1.5 py-1">
                    <div className="text-ink-muted">MapSpec</div>
                    <div className={sn.diff.mapspecChanged ? 'text-status-warning' : 'text-ink'}>
                      {sn.diff.mapspecChanged ? '可用性变化' : '一致'}
                    </div>
                  </div>
                </div>
                {(sn.diff.artifacts.missingAdded.length > 0 || sn.diff.artifacts.missingRemoved.length > 0) && (
                  <p className="text-status-critical">
                    {t('p0P13', { p0: sn.diff.artifacts.missingAdded.length, p1: sn.diff.artifacts.missingRemoved.length })}</p>
                )}
                {(sn.diff.layers.missingAdded.length > 0 || sn.diff.layers.missingRemoved.length > 0) && (
                  <p className="text-status-critical">
                    {t('p0P14', { p0: sn.diff.layers.missingAdded.length, p1: sn.diff.layers.missingRemoved.length })}</p>
                )}
                {sn.diff.integrityChanges.length > 0 && (
                  <details>
                    <summary className="cursor-pointer select-none text-ink-secondary">
                      {t('kki4p9p', { p0: sn.diff.integrityChanges.length })}</summary>
                    <ul className="mt-1 space-y-0.5 font-mono text-ink-secondary">
                      {sn.diff.integrityChanges.slice(0, 8).map((c) => (
                        <li key={c.id}>
                          {shortId(c.id, 10)}: {c.from} → {c.to}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {cloneTarget && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
          <p className="text-meta font-medium text-ink">
            {t('k1kzl2or', { p0: cloneTarget.label || shortId(cloneTarget.snapshot_id, 10) })}</p>
          <SField
            label={t('idTargetSessionId')}
            value={cloneTargetSession}
            onChange={setCloneTargetSession}
            placeholder={t('k193n26q')}
            hint={`源会话：${shortId(sessionId, 16)}`}
          />
          <div className="flex gap-1.5">
            <ConfirmAction
              label={t('kfjn5')}
              confirmLabel={t('k1m2adxe')}
              onConfirm={() => {
                void handleClone();
              }}
              disabled={sn.busyId === cloneTarget.snapshot_id || !cloneTargetSession.trim()}
            />
            <button
              type="button"
              onClick={() => setCloneTarget(null)}
              className="rounded-sm border border-edge-subtle px-2 py-0.5 text-caption text-ink-secondary hover:bg-surface-sunken"
            >
              {t('kfs4e')}</button>
          </div>
        </div>
      )}

      {restoreTarget && (
        <div className="space-y-2 rounded-md border border-status-warning-border bg-status-warning-soft px-panel py-2.5">
          <p className="text-meta font-medium text-ink">
            {t('keix4ex', { p0: restoreTarget.label || shortId(restoreTarget.snapshot_id, 10) })}</p>
          <p className="text-micro text-ink-secondary">
            {t('verifyRegister')}</p>
          <label className="block space-y-1">
            <span className="block text-meta font-medium text-ink-secondary">{t('kf393un')}</span>
            <select
              value={restoreMode}
              onChange={(e) => setRestoreMode(e.target.value as typeof restoreMode)}
              className="w-full rounded-sm border border-edge-subtle bg-surface-raised px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
            >
              <option value="verify">{t('verify')}</option>
              <option value="register">{t('register')}</option>
            </select>
          </label>
          <div className="flex gap-1.5">
            <ConfirmAction
              label={restoreMode === 'register' ? '执行恢复' : '开始核查'}
              confirmLabel={restoreMode === 'register' ? '确认覆盖当前变更？' : '确认核查？'}
              onConfirm={() => {
                void handleRestore();
              }}
              disabled={sn.busyId === restoreTarget.snapshot_id}
            />
            <button
              type="button"
              onClick={() => setRestoreTarget(null)}
              className="rounded-sm border border-edge-subtle px-2 py-0.5 text-caption text-ink-secondary hover:bg-surface-sunken"
            >
              {t('kfs4e2')}</button>
          </div>
        </div>
      )}

      <p className="text-micro text-ink-muted">
        {t('khi0gjs', { p0: sn.bounded })}</p>
    </section>
  );
}
