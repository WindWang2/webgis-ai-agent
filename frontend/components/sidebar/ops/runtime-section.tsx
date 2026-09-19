'use client';

/**
 * 运行时分区（P1 —— runtime-inspector.tsx 接线复活，ADR-0142 D2）。
 *
 * 复活方式：实例列表（GET /workflow-runtime/instances，≤32/次）→ 选中实例
 * → 注入 fetcher=(id) => getInstance(id)（/instances/{id} 自带 explain）→
 * <RuntimeInspector> 首次出现在真实 UI 中。组件本体契约不动；周边补 V6
 * 干预：实例取消（ConfirmDialog）、FAILED/STALE 节点重试（force 可选）、
 * recompute-plan 视图、debug 摘要。
 */
import { useEffect, useMemo, useState } from 'react';
import { RotateCcw, XCircle, GitBranch, Bug } from 'lucide-react';
import { RuntimeInspector } from '@/components/sidebar/workflow/runtime-inspector';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { StatusBadge } from '@/components/shared/status-badge';
import { InlineNotice } from '@/components/shared/inline-notice';
import {
  getInstance,
  getRecomputePlan,
  listInstances,
  retryNode,
  cancelInstance,
  WorkflowRuntimeApiError,
  type RecomputePlanView,
  type WorkflowInstanceRow,
} from '@/lib/api/workflow-runtime';
import { useBoundedPoll } from '@/lib/hooks/use-cluster-poll';
import { OpsCard, PermissionNotice, formatTime } from './ops-shared';
import { useT } from '@/lib/i18n/useT';

export function RuntimeSection({
  ownerToken,
  sessionId,
}: {
  ownerToken?: string | null;
  sessionId?: string | null;
}) {
  const t = useT('ops');
  const instances = useBoundedPoll<{ rows: WorkflowInstanceRow[] }>({
    pollIntervalMs: 6000,
    fetcher: async (signal) => {
      const rows = await listInstances({ ownerToken, signal });
      return { rows };
    },
  });

  const [selected, setSelected] = useState<string | null>(null);
  const [planShown, setPlanShown] = useState(false);
  const rows = useMemo(() => instances.data?.rows ?? [], [instances.data]);
  useEffect(() => {
    if (!selected && rows.length > 0) {
      setSelected(rows[0].instance_id);
      setPlanShown(false);
    }
    if (selected && !rows.some((r) => r.instance_id === selected)) {
      setSelected(rows[0]?.instance_id ?? null);
      setPlanShown(false);
    }
  }, [rows, selected]);

  const detail = useBoundedPoll<{ instance: Awaited<ReturnType<typeof getInstance>> | null }>({
    enabled: selected != null,
    resetKey: selected ?? 'none',
    pollIntervalMs: 6000,
    fetcher: async (signal) => {
      try {
        const instance = await getInstance(selected as string, { ownerToken, signal });
        return { instance };
      } catch {
        return { instance: null };
      }
    },
  });

  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);

  // recompute-plan 视图（选中实例变化即取一次，不轮询 —— 规划视图是请求时点快照）。
  // 注意：不在本 effect 里 setPlanShown(false)。该 effect 的 cleanup/run 发生在
  // paint 之后；若测试/用户在按钮出现后立刻点击，晚到的 setPlanShown(false)
  // 会吞掉这次展开（CI 全量套件下更容易踩中，见 #1318 Frontend Tests）。
  // 折叠只在「选中实例真正切换」时发生（列表点击 / 自动选中 effect）。
  const [plan, setPlan] = useState<RecomputePlanView | null>(null);
  useEffect(() => {
    setPlan(null);
    if (!selected) return;
    let cancelled = false;
    getRecomputePlan(selected, { ownerToken })
      .then((view) => {
        if (!cancelled) setPlan(view);
      })
      .catch(() => {
        if (!cancelled) setPlan(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, ownerToken]);

  const actionableNodes = useMemo(() => {
    const inst = detail.data?.instance;
    if (!inst) return [];
    return inst.nodes.filter((n) => ['FAILED', 'STALE', 'CANCELLED'].includes(n.state));
  }, [detail.data]);

  const runAction = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(
        err instanceof WorkflowRuntimeApiError
          ? `${err.code}：${err.message}`
          : '操作失败',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3" data-testid="ops-runtime-section">
      <OpsCard
        title={t('kwzdd6j')}
        sub={t('workflowRuntimeV5V6Durable')}
        actions={
          <button
            type="button"
            onClick={() => instances.refresh()}
            className="rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro font-medium text-ink-secondary hover:bg-surface-hover"
          >
            {t('kfg072')}</button>
        }
        testId="ops-runtime-instances"
      >
        {rows.length === 0 ? (
          <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
            {instances.loading ? '正在加载…' : '当前无运行时实例'}
          </p>
        ) : (
          <ul className="flex flex-col gap-1" aria-label={t('k1bdvmze')}>
            {rows.map((row) => (
              <li key={row.instance_id}>
                <button
                  type="button"
                  data-testid={`instance-${row.instance_id}`}
                  aria-pressed={selected === row.instance_id}
                  onClick={() => { setSelected(row.instance_id); setPlanShown(false); }}
                  className={`flex w-full items-center justify-between gap-2 rounded-sm border px-2 py-1 text-left text-micro transition-colors ${
                    selected === row.instance_id
                      ? 'border-status-accent-border bg-status-accent-soft'
                      : 'border-edge-subtle bg-surface-sunken hover:bg-surface-hover'
                  }`}
                >
                  <span className="truncate font-mono text-ink" title={row.instance_id}>
                    {row.instance_id}
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    <span className="max-w-[8rem] truncate text-ink-muted" title={row.package_id}>
                      {row.package_id}
                    </span>
                    <StatusBadge status={row.status} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </OpsCard>

      {selected && (
        <OpsCard
          title={t('k1af0s8m')}
          sub={`${selected} · 每 6s 轮询投影`}
          actions={
            <span className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPlanShown((v) => !v)}
                aria-expanded={planShown}
                className="flex items-center gap-1 rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro font-medium text-ink-secondary hover:bg-surface-hover"
              >
                <GitBranch size={11} aria-hidden />
                {t('kmrtz2v')}</button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmCancel(true)}
                className="flex items-center gap-1 rounded-sm border border-status-critical-border bg-status-critical-soft px-1.5 py-0.5 text-micro font-medium text-status-critical disabled:opacity-50"
              >
                <XCircle size={11} aria-hidden />
                {t('kd9upon')}</button>
            </span>
          }
          testId="ops-runtime-inspector"
        >
          {/* —— P1 复活点：此前零调用方的组件，从这条注入开始进入真实 UI —— */}
          <RuntimeInspector
            instanceId={selected}
            fetcher={(id) => getInstance(id, { ownerToken })}
          />
          <p className="text-micro text-ink-muted">
            {t('k3wlhb9', { p0: detail.status.lastFetchedAt ? formatTime(detail.status.lastFetchedAt) : '…' })}</p>

          {planShown && (
            <div className="rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1.5" data-testid="recompute-plan">
              {plan ? (
                <>
                  <p className="text-micro font-medium text-ink-secondary">
                    stale {plan.stale} {t('p0P13', { p0: (plan.recompute ?? []).length, p1: (plan.reuse ?? []).length })}</p>
                  {(plan.explanations ?? []).slice(0, 3).map((exp, i) => (
                    <p key={i} className="text-micro text-ink-muted">· {exp}</p>
                  ))}
                  <p className="text-micro text-ink-muted">
                    {t('k1dym8nq', { p0: (plan.recompute ?? []).join('、') || '—' })}</p>
                </>
              ) : (
                <p className="text-micro text-ink-muted">{t('k15uwkm6')}</p>
              )}
            </div>
          )}

          {/* V6 干预：失败/过期节点重试 */}
          {actionableNodes.length > 0 && (
            <div className="flex flex-col gap-1" aria-label={t('kabvoo0')}>
              <p className="text-micro font-medium text-ink-secondary">{t('kke1p4h')}</p>
              {actionableNodes.map((n) => (
                <div key={n.node_id} className="flex items-center justify-between gap-2 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <StatusBadge status={n.state.toLowerCase()} label={n.state} />
                    <span className="truncate font-mono text-micro text-ink-secondary" title={n.node_id}>
                      {n.node_id}
                    </span>
                    {n.error_code && <span className="shrink-0 text-micro text-status-critical">{n.error_code}</span>}
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    data-testid={`retry-${n.node_id}`}
                    onClick={() =>
                      void runAction(() => retryNode(selected, n.node_id, { ownerToken, force: n.state === 'CANCELLED' }))
                    }
                    className="flex shrink-0 items-center gap-1 rounded-sm border border-status-info-border bg-status-info-soft px-1.5 py-0.5 text-micro font-medium text-status-info disabled:opacity-50"
                  >
                    <RotateCcw size={11} aria-hidden />
                    {t('k1y5iyuy', { p0: n.attempts > 0 ? `（${n.attempts}）` : '' })}</button>
                </div>
              ))}
            </div>
          )}

          {actionError && <InlineNotice variant="error">{actionError}</InlineNotice>}
          <p className="flex items-center gap-1 text-micro text-ink-muted">
            <Bug size={11} aria-hidden />
            {t('getInstancesP0DebugRecent', { p0: selected })}</p>
        </OpsCard>
      )}

      <ConfirmDialog
        open={confirmCancel}
        title={t('kmfpjst')}
        description={t('kqx8p9c')}
        confirmLabel={t('kd9upon2')}
        onConfirm={() => {
          setConfirmCancel(false);
          if (selected) void runAction(() => cancelInstance(selected, { ownerToken }));
        }}
        onCancel={() => setConfirmCancel(false)}
      />

      {instances.lastError instanceof WorkflowRuntimeApiError &&
        instances.lastError.status === 404 && (
          <PermissionNotice description={t('owner')} />
        )}
      {sessionId == null && (
        <p className="text-micro text-ink-muted">{t('owner2')}</p>
      )}
    </div>
  );
}
