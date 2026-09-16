'use client';

/**
 * Mission 视图：组织内未终结 mission 列表 + 选中 mission 的投影详情 +
 * 安全 operator 动作 + checkpoint 时间线。
 *
 * 纪律：
 * - 全部状态来自 /cockpit 投影（revision 单调守卫见 useCockpitProjection）；
 * - operator 动作：两步确认 + 常量 worker_id（"ops-console"）+ 无乐观写。
 *   成功 = 触发投影刷新；409/其他错误只呈现错误，不改视图状态；
 * - 按钮可用性是服务端 TRANSITIONS 的**镜像**（仅防误点）；裁决权始终在
 *   服务端（409 fencing）。
 */
import { useState } from 'react';
import {
  useCockpitProjection,
} from '@/lib/cockpit/use-cockpit-projection';
import {
  listCockpitMissions,
  getCockpitMission,
  getCockpitTimeline,
  startCockpitMission,
  suspendCockpitMission,
  resumeCockpitMission,
  cancelCockpitMission,
  missionAllows,
  type CockpitMissionSummary,
  type CockpitMissionDetail,
  type CockpitTimeline,
} from '@/lib/api/cockpit';
import { ApiError, describeApiError } from '@/lib/api/transport';
import { useT } from '@/lib/i18n/useT';
import { StatusBadge, Kv, EmptyState, VirtualList } from './cockpit-shared';

type Op = 'start' | 'suspend' | 'resume' | 'cancel';

const OPS: readonly Op[] = ['start', 'suspend', 'resume', 'cancel'];

export interface MissionViewProps {
  enabled: boolean;
  ownerToken?: string | null;
  /** 受控：选中 mission 由 root 持有（execution/resources 视图共享）。 */
  selectedId: string | null;
  onSelect: (missionId: string | null) => void;
}

function fmtTime(ts: number | null | undefined, locale: string): string {
  if (typeof ts !== 'number' || ts <= 0) return '—';
  try {
    return new Date(ts * 1000).toLocaleString(locale);
  } catch {
    return String(ts);
  }
}

export function MissionView({ enabled, ownerToken, selectedId, onSelect }: MissionViewProps) {
  const t = useT('cockpit');
  const [armed, setArmed] = useState<Op | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [bump, setBump] = useState(0); // 动作成功后的重新投影触发器

  const list = useCockpitProjection(
    (signal) =>
      listCockpitMissions({ signal, ownerToken }),
    { enabled, resetKey: `missions#${bump}` },
  );

  const detail = useCockpitProjection(
    (signal) => getCockpitMission(selectedId as string, { signal, ownerToken }),
    {
      enabled: enabled && selectedId !== null,
      resetKey: selectedId ?? 'none',
      revisionOf: (d: CockpitMissionDetail) => d.mission?.revision ?? 0,
    },
  );

  const timeline = useCockpitProjection(
    (signal) =>
      getCockpitTimeline(selectedId as string, { signal, ownerToken, limit: 32 }),
    {
      enabled: enabled && selectedId !== null,
      resetKey: selectedId ?? 'none',
      revisionOf: (d: CockpitTimeline) => d.revision ?? 0,
    },
  );

  const missions = list.data?.missions ?? [];

  async function runOp(op: Op) {
    if (!selectedId || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      const args = { ownerToken } as const;
      if (op === 'start') await startCockpitMission(selectedId, args);
      else if (op === 'suspend') await suspendCockpitMission(selectedId, args);
      else if (op === 'resume') await resumeCockpitMission(selectedId, args);
      else await cancelCockpitMission(selectedId, args);
      setArmed(null);
      setBump((n) => n + 1); // 无乐观写：动作成功后只触发重新投影
    } catch (err) {
      setArmed(null);
      if (err instanceof ApiError && err.status === 409) {
        setActionError(`409 · ${t('action.conflict')}`);
      } else {
        setActionError(`${t('action.error')}: ${describeApiError(err, String(err))}`);
      }
    } finally {
      setActionBusy(false);
    }
  }

  const state = detail.data?.mission?.state ?? '';
  const stateLabel = (s: string) => t(`state.${s}`);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="cockpit-mission-view">
      {/* Mission 列表 */}
      {missions.length === 0 ? (
        <EmptyState testId="cockpit-missions-empty">
          {list.loading ? t('empty.noData') : t('empty.noMissionList')}
        </EmptyState>
      ) : (
        <ul className="flex flex-col gap-1" aria-label={t('view.mission')}>
          {missions.map((m: CockpitMissionSummary) => (
            <li key={m.mission_id}>
              <button
                type="button"
                data-testid={`cockpit-mission-${m.mission_id}`}
                onClick={() => { onSelect(m.mission_id); setArmed(null); setActionError(null); }}
                aria-pressed={selectedId === m.mission_id}
                className={`flex w-full flex-col gap-0.5 rounded-sm border px-2 py-1.5 text-left text-meta transition-colors ${
                  selectedId === m.mission_id
                    ? 'border-status-accent-border bg-status-accent-soft'
                    : 'border-edge-subtle hover:bg-surface-hover'
                }`}
              >
                <span className="flex items-center gap-2">
                  <StatusBadge state={m.state} label={stateLabel(m.state)} />
                  <span className="min-w-0 truncate font-medium text-ink">{m.root_goal || m.mission_id}</span>
                </span>
                <span className="text-ink-muted">
                  rev {m.revision} · g{m.goal_revision}
                  {' · '}
                  {t('mission.completed')} {m.frontier_counts.completed ?? 0} / {t('mission.failed')} {m.frontier_counts.failed ?? 0}
                  {m.blocked_reason ? ` · ⚠ ${m.blocked_reason}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Mission 详情投影 */}
      {selectedId === null ? (
        <EmptyState testId="cockpit-no-mission-selected" note={t('mission.processLocalNote')}>
          {t('empty.noMission')}
        </EmptyState>
      ) : detail.data ? (
        <section aria-label={t('view.mission')} className="flex min-h-0 flex-col gap-1.5 rounded-sm border border-edge-subtle p-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              state={detail.data.mission.state}
              label={stateLabel(detail.data.mission.state)}
            />
            <span data-testid="cockpit-mission-state" className="sr-only">{detail.data.mission.state}</span>
            <span className="text-meta text-ink-muted">
              <span data-testid="cockpit-mission-revision">rev {detail.data.mission.revision}</span>
              {' · '}g{detail.data.mission.goal_revision}
            </span>
            <span className="ms-auto flex items-center gap-1">
              {OPS.filter((op) => missionAllows(state, op)).map((op) => (
                armed === op ? (
                  <button
                    key={`confirm-${op}`}
                    type="button"
                    data-testid={`cockpit-action-${op}-confirm`}
                    disabled={actionBusy}
                    onClick={() => void runOp(op)}
                    onBlur={() => setArmed(null)}
                    className="rounded-sm border border-status-critical-border bg-status-critical-soft px-2 py-0.5 text-meta font-medium text-status-critical disabled:opacity-50"
                  >
                    {t(`action.${op}`)}{t('action.confirmSuffix')}
                  </button>
                ) : (
                  <button
                    key={op}
                    type="button"
                    data-testid={`cockpit-action-${op}`}
                    disabled={actionBusy}
                    onClick={() => setArmed(op)}
                    className="rounded-sm border border-edge-subtle px-2 py-0.5 text-meta text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
                  >
                    {t(`action.${op}`)}
                  </button>
                )
              ))}
            </span>
          </div>

          {actionError ? (
            <p data-testid="cockpit-action-error" role="alert" className="text-meta text-status-critical">
              {actionError}
            </p>
          ) : null}

          <p className="text-meta font-medium text-ink">{detail.data.mission.root_goal || detail.data.mission.mission_id}</p>

          <div className="grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
            <Kv label={t('mission.lease')}>
              {detail.data.mission.lease_owner || '—'} (epoch {detail.data.mission.lease_epoch})
            </Kv>
            <Kv label={t('mission.blocked')}>
              {detail.data.mission.recovery.blocked_reason || t('mission.none')}
            </Kv>
            <Kv label={t('mission.createdAt')}>{fmtTime(detail.data.mission.created_at, 'zh-CN')}</Kv>
            <Kv label={t('mission.updatedAt')}>{fmtTime(detail.data.mission.updated_at, 'zh-CN')}</Kv>
          </div>

          {/* 工作前沿 */}
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-meta text-ink-muted" data-testid="cockpit-frontier">
            {(['completed', 'running', 'pending', 'failed', 'blocked'] as const).map((bucket) => (
              <span key={bucket}>
                {t(`mission.${bucket}`)}: {(detail.data?.mission.frontier?.[bucket] ?? []).length}
              </span>
            ))}
            {detail.data.mission.recovery.unresolved_ops.length > 0 ? (
              <span className="text-status-critical">
                UNRESOLVED × {detail.data.mission.recovery.unresolved_ops.length}
              </span>
            ) : null}
          </div>

          {/* 引用（refs 只读） */}
          <Kv label={t('mission.refs')}>
            <span className="break-all">
              {Object.entries(detail.data.mission.refs)
                .filter(([, v]) => (v ?? []).length > 0)
                .map(([k, v]) => `${k}×${v.length}`)
                .join(' · ') || t('mission.none')}
            </span>
          </Kv>

          {/* checkpoint 时间线（窗口化） */}
          <p className="text-meta font-medium text-ink-secondary">{t('mission.checkpoints')}</p>
          {timeline.data && timeline.data.checkpoints.length > 0 ? (
            <VirtualList
              items={timeline.data.checkpoints}
              rowHeight={44}
              rowTestId="cockpit-checkpoint-row"
              ariaLabel={t('mission.checkpoints')}
              renderRow={(cp) => (
                <div className="flex h-full items-center gap-2 border-b border-edge-subtle px-1 text-meta">
                  <StatusBadge state={cp.state} label={stateLabel(cp.state)} />
                  <span className="text-ink-secondary">{cp.checkpoint_id}</span>
                  <span className="ms-auto text-ink-muted">
                    rev {cp.mission_revision} · g{cp.goal_revision} · {fmtTime(cp.created_at, 'zh-CN')}
                  </span>
                </div>
              )}
            />
          ) : (
            <EmptyState testId="cockpit-timeline-empty">{t('empty.noData')}</EmptyState>
          )}
        </section>
      ) : (
        <EmptyState testId="cockpit-mission-loading">{t('empty.noData')}</EmptyState>
      )}
    </div>
  );
}
