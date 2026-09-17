'use client';

/**
 * 执行 / 资源视图：Swarm durable ledger 投影 + mission 资源预算投影。
 *
 * Swarm 任务态原样呈现服务端 durable 词表（含 UNRESOLVED——destructive
 * unknown，绝不盲目重跑）；资源视图只读呈现配额/消耗/预留，超限背压
 * 由后端裁决（拒绝新预留），前端仅展示。
 */
import {
  useCockpitProjection,
} from '@/lib/cockpit/use-cockpit-projection';
import {
  getCockpitSwarm,
  getCockpitMission,
  type CockpitMissionDetail,
} from '@/lib/api/cockpit';
import { useT } from '@/lib/i18n/useT';
import { StatusBadge, EmptyState } from './cockpit-shared';

export interface SwarmViewProps {
  enabled: boolean;
  missionId: string | null;
  ownerToken?: string | null;
}

export function SwarmView({ enabled, missionId, ownerToken }: SwarmViewProps) {
  const t = useT('cockpit');
  const swarm = useCockpitProjection(
    (signal) => getCockpitSwarm(missionId as string, { signal, ownerToken }),
    { enabled: enabled && missionId !== null, resetKey: missionId ?? 'none' },
  );

  if (!missionId) {
    return <EmptyState testId="cockpit-swarm-no-mission">{t('empty.noMission')}</EmptyState>;
  }
  const runs = swarm.data?.runs ?? [];
  if (runs.length === 0) {
    return <EmptyState testId="cockpit-swarm-empty">{swarm.loading ? t('empty.noData') : t('empty.noData')}</EmptyState>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="cockpit-swarm-view">
      {runs.map((run) => {
        const tasks = Object.values(run.tasks ?? {});
        const byState = tasks.reduce<Record<string, number>>((acc, task) => {
          acc[task.state] = (acc[task.state] ?? 0) + 1;
          return acc;
        }, {});
        return (
          <section key={run.swarm_run_id} aria-label={t('swarm.runs')} className="flex flex-col gap-1 rounded-sm border border-edge-subtle p-2">
            <div className="flex flex-wrap items-center gap-2 text-meta">
              <StatusBadge state={run.state} label={run.state} />
              <span className="font-medium text-ink">{run.swarm_run_id}</span>
              <span className="text-ink-muted">{t('swarm.goalSlice')}: {run.goal_slice || '—'}</span>
            </div>
            <div className="flex flex-wrap gap-x-3 text-meta text-ink-muted">
              {Object.entries(byState).map(([st, n]) => (
                <span key={st}>
                  {t(`swarm.taskState.${st}`)}: {n}
                </span>
              ))}
            </div>
            <table className="w-full text-meta" data-testid="cockpit-swarm-tasks">
              <thead>
                <tr className="text-left text-ink-muted">
                  <th className="py-0.5 font-medium">task</th>
                  <th className="py-0.5 font-medium">{t('swarm.state')}</th>
                  <th className="py-0.5 font-medium">class</th>
                  <th className="py-0.5 font-medium">refs</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.task_id} className="border-t border-edge-subtle">
                    <td className="py-0.5 text-ink">{task.task_id} <span className="text-ink-muted">×{task.attempt}</span></td>
                    <td className="py-0.5">
                      <StatusBadge state={task.state} label={t(`swarm.taskState.${task.state}`)} />
                    </td>
                    <td className="py-0.5 text-ink-muted">{task.operation_class}</td>
                    <td className="py-0.5 text-ink-muted">{(task.produced_refs ?? []).length > 0 ? task.produced_refs.join(', ') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
    </div>
  );
}

export interface ResourcesViewProps {
  enabled: boolean;
  missionId: string | null;
  ownerToken?: string | null;
}

function budgetRows(
  budget: CockpitMissionDetail['mission']['resource_budget'] | undefined,
): Array<{ dim: string; quota: number | null; consumed: number; reserved: number; retry: number }> {
  if (!budget) return [];
  const dims = new Set<string>([
    ...Object.keys(budget.quota ?? {}),
    ...Object.keys(budget.consumed ?? {}),
    ...Object.keys(budget.reserved ?? {}),
  ]);
  return [...dims].map((dim) => ({
    dim,
    quota: typeof budget.quota?.[dim] === 'number' ? budget.quota[dim] : null,
    consumed: budget.consumed?.[dim] ?? 0,
    reserved: budget.reserved?.[dim] ?? 0,
    retry: budget.retry_cost?.[dim] ?? 0,
  }));
}

export function ResourcesView({ enabled, missionId, ownerToken }: ResourcesViewProps) {
  const t = useT('cockpit');
  const detail = useCockpitProjection(
    (signal) => getCockpitMission(missionId as string, { signal, ownerToken }),
    {
      enabled: enabled && missionId !== null,
      resetKey: missionId ?? 'none',
      revisionOf: (d: CockpitMissionDetail) => d.mission?.revision ?? 0,
    },
  );

  if (!missionId) {
    return <EmptyState testId="cockpit-resources-no-mission">{t('empty.noMission')}</EmptyState>;
  }
  const rows = budgetRows(detail.data?.mission?.resource_budget);
  if (rows.length === 0) {
    return <EmptyState testId="cockpit-resources-empty">{t('empty.noData')}</EmptyState>;
  }
  const exhausted = rows
    .filter((r) => r.quota !== null && r.quota > 0 && r.consumed + r.reserved >= r.quota)
    .map((r) => r.dim);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="cockpit-resources-view">
      {exhausted.length > 0 ? (
        <p role="alert" className="text-meta text-status-critical">
          {t('resources.exhausted')}: {exhausted.join(', ')}
        </p>
      ) : null}
      <table className="w-full text-meta" data-testid="cockpit-resources-table">
        <thead>
          <tr className="text-left text-ink-muted">
            <th className="py-0.5 font-medium">dim</th>
            <th className="py-0.5 font-medium">{t('resources.quota')}</th>
            <th className="py-0.5 font-medium">{t('resources.consumed')}</th>
            <th className="py-0.5 font-medium">{t('resources.reserved')}</th>
            <th className="py-0.5 font-medium">{t('resources.retryCost')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const over = r.quota !== null && r.quota > 0 && r.consumed + r.reserved >= r.quota;
            return (
              <tr key={r.dim} className="border-t border-edge-subtle">
                <td className="py-0.5 text-ink">{r.dim}</td>
                <td className="py-0.5 text-ink-muted">{r.quota ?? '—'}</td>
                <td className={`py-0.5 ${over ? 'text-status-critical' : 'text-ink-muted'}`}>{r.consumed}</td>
                <td className="py-0.5 text-ink-muted">{r.reserved}</td>
                <td className="py-0.5 text-ink-muted">{r.retry}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-meta text-ink-muted">{t('resources.backpressure')}</p>
    </div>
  );
}
