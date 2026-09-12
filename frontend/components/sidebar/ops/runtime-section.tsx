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

export function RuntimeSection({
  ownerToken,
  sessionId,
}: {
  ownerToken?: string | null;
  sessionId?: string | null;
}) {
  const instances = useBoundedPoll<{ rows: WorkflowInstanceRow[] }>({
    pollIntervalMs: 6000,
    fetcher: async (signal) => {
      const rows = await listInstances({ ownerToken, signal });
      return { rows };
    },
  });

  const [selected, setSelected] = useState<string | null>(null);
  const rows = useMemo(() => instances.data?.rows ?? [], [instances.data]);
  useEffect(() => {
    if (!selected && rows.length > 0) setSelected(rows[0].instance_id);
    if (selected && !rows.some((r) => r.instance_id === selected)) setSelected(rows[0]?.instance_id ?? null);
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
  const [plan, setPlan] = useState<RecomputePlanView | null>(null);
  const [planShown, setPlanShown] = useState(false);
  useEffect(() => {
    setPlan(null);
    setPlanShown(false);
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
        title="运行时实例"
        sub="workflow-runtime V5/V6 durable 实例（≤32/页）"
        actions={
          <button
            type="button"
            onClick={() => instances.refresh()}
            className="rounded-sm border border-edge-subtle px-1.5 py-0.5 text-micro font-medium text-ink-secondary hover:bg-surface-hover"
          >
            刷新
          </button>
        }
        testId="ops-runtime-instances"
      >
        {rows.length === 0 ? (
          <p className="px-2 py-3 text-center text-meta text-ink-muted" role="status">
            {instances.loading ? '正在加载…' : '当前无运行时实例'}
          </p>
        ) : (
          <ul className="flex flex-col gap-1" aria-label="运行时实例列表">
            {rows.map((row) => (
              <li key={row.instance_id}>
                <button
                  type="button"
                  data-testid={`instance-${row.instance_id}`}
                  aria-pressed={selected === row.instance_id}
                  onClick={() => setSelected(row.instance_id)}
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
          title="实例检查器"
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
                重算计划
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmCancel(true)}
                className="flex items-center gap-1 rounded-sm border border-status-critical-border bg-status-critical-soft px-1.5 py-0.5 text-micro font-medium text-status-critical disabled:opacity-50"
              >
                <XCircle size={11} aria-hidden />
                取消实例
              </button>
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
            投影刷新 {detail.status.lastFetchedAt ? formatTime(detail.status.lastFetchedAt) : '…'}
          </p>

          {planShown && (
            <div className="rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1.5" data-testid="recompute-plan">
              {plan ? (
                <>
                  <p className="text-micro font-medium text-ink-secondary">
                    stale {plan.stale} · 重算 {(plan.recompute ?? []).length} · 复用 {(plan.reuse ?? []).length}
                  </p>
                  {(plan.explanations ?? []).slice(0, 3).map((exp, i) => (
                    <p key={i} className="text-micro text-ink-muted">· {exp}</p>
                  ))}
                  <p className="text-micro text-ink-muted">
                    重算：{(plan.recompute ?? []).join('、') || '—'}
                  </p>
                </>
              ) : (
                <p className="text-micro text-ink-muted">重算规划不可用</p>
              )}
            </div>
          )}

          {/* V6 干预：失败/过期节点重试 */}
          {actionableNodes.length > 0 && (
            <div className="flex flex-col gap-1" aria-label="可干预节点">
              <p className="text-micro font-medium text-ink-secondary">节点干预</p>
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
                    重试{n.attempts > 0 ? `（${n.attempts}）` : ''}
                  </button>
                </div>
              ))}
            </div>
          )}

          {actionError && <InlineNotice variant="error">{actionError}</InlineNotice>}
          <p className="flex items-center gap-1 text-micro text-ink-muted">
            <Bug size={11} aria-hidden />
            深度诊断面：GET /instances/{selected}/debug（recent_events + children）已由 typed client 提供，本分区展示投影摘要。
          </p>
        </OpsCard>
      )}

      <ConfirmDialog
        open={confirmCancel}
        title="取消该运行时实例？"
        description="实例将请求取消；运行中的节点经租约安全回收。终态实例不可再取消（幂等回执）。"
        confirmLabel="取消实例"
        onConfirm={() => {
          setConfirmCancel(false);
          if (selected) void runAction(() => cancelInstance(selected, { ownerToken }));
        }}
        onCancel={() => setConfirmCancel(false)}
      />

      {instances.lastError instanceof WorkflowRuntimeApiError &&
        instances.lastError.status === 404 && (
          <PermissionNotice description="实例列表不可见（owner 隔离或会话失效）。" />
        )}
      {sessionId == null && (
        <p className="text-micro text-ink-muted">未携带会话上下文：实例列表按当前账号 owner 域隔离。</p>
      )}
    </div>
  );
}
