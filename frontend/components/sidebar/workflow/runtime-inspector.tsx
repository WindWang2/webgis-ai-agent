'use client';

import { useEffect, useState } from 'react';
import { Workflow } from 'lucide-react';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { useT } from '@/lib/i18n/useT';

/**
 * Workflow Runtime V5 inspector（minimal wiring，Epic workflow-v5 §11）。
 *
 * 消费 /api/v1/workflow-runtime 实例投影：方法论族、节点状态徽章、
 * blocked 原因、stale/reused 计数、why_recomputed/why_reused 解释。
 * 只读投影组件 —— 数据获取由宿主面板传入（fetcher 注入便于测试）。
 */

export interface RuntimeNode {
  node_id: string;
  state: string;
  attempts: number;
  /** 后端节点投影 error_code 可空（V6 投影）—— 契约漂移修正（ADR-0142 D2）。 */
  error_code: string | null;
  reused: boolean;
  binding_violations: string[];
}

export interface RuntimeInstance {
  instance_id: string;
  package_id: string;
  package_version: string;
  status: string;
  methodology_family?: string;
  nodes: RuntimeNode[];
  counts: Record<string, number>;
  explain?: {
    why_recomputed: string[];
    why_reused: string[];
    blocked: { node: string; codes: string[] }[];
  };
}

export interface RuntimeInspectorProps {
  instanceId: string;
  fetcher?: (instanceId: string) => Promise<RuntimeInstance>;
}

const STATE_BADGE: Record<string, string> = {
  SUCCEEDED: 'text-emerald-600',
  RUNNING: 'text-blue-600',
  FAILED: 'text-red-600',
  BLOCKED: 'text-amber-600',
  STALE: 'text-orange-500',
  CANCELLED: 'text-gray-500',
  SKIPPED: 'text-gray-400',
  READY: 'text-sky-600',
  PENDING: 'text-gray-400',
};

export function RuntimeInspector({ instanceId, fetcher }: RuntimeInspectorProps) {
const t = useT();
  const [state, setState] = useState<
    RuntimeInstance | 'loading' | 'error' | undefined
  >(undefined);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!fetcher) return;
      setState('loading');
      try {
        const data = await fetcher(instanceId);
        if (!cancelled) setState(data);
      } catch {
        if (!cancelled) setState('error');
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [instanceId, fetcher]);

  if (!state) {
    return (
      <button
        type="button"
        onClick={() => setState('loading')}
        className="rounded px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-hover)]"
      >
        {t('sidebar.wf.loadRuntime')}
      </button>
    );
  }
  if (state === 'loading') return <LoadingState label={t('sidebar.wf.loadingRuntime')} />;
  if (state === 'error') return <InlineNotice variant="error">{t('sidebar.wf.runtimeFailed')}</InlineNotice>;

  const inst = state as RuntimeInstance;
  if (inst.nodes.length === 0) {
    return <EmptyState icon={Workflow} title={t('sidebar.wf.noNodes')} description={t('sidebar.wf.noNodesDesc')} />;
  }

  return (
    <div className="space-y-2" aria-label={t('sidebar.wf.runtimeAria')}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-[var(--theme-text-secondary)]">
          {inst.methodology_family || inst.package_id} v{inst.package_version}
        </span>
        <span data-testid="wf-status" className="text-[10px] text-[var(--theme-text-muted)]">
          {inst.status}
        </span>
      </div>
      <ul className="space-y-1" aria-label={t('sidebar.wf.nodeStatusAria')}>
        {inst.nodes.slice(0, 16).map((n) => (
          <li key={n.node_id} className="flex items-center gap-2 text-[11px]">
            <span data-testid={`wf-node-${n.node_id}`} className={STATE_BADGE[n.state] ?? ''}>
              {n.state}
            </span>
            <span className="truncate text-[var(--theme-text-secondary)]" title={n.node_id}>
              {n.node_id}
            </span>
            {n.reused && (
              <span className="rounded bg-[var(--theme-bg-hover)] px-1 text-[9px]">{t('sidebar.wf.reused')}</span>
            )}
            {n.binding_violations.length > 0 && (
              <span className="text-[9px] text-amber-600">{n.binding_violations[0]}</span>
            )}
          </li>
        ))}
      </ul>
      {inst.explain && inst.explain.why_recomputed.length > 0 && (
        <div className="text-[10px] text-[var(--theme-text-muted)]">
          {inst.explain.why_recomputed.slice(0, 2).map((w, i) => (
            <p key={`rc-${i}`}>{t('sidebar.wf.recomputed')}{w}</p>
          ))}
        </div>
      )}
      {inst.explain && inst.explain.why_reused.length > 0 && (
        <div className="text-[10px] text-[var(--theme-text-muted)]">
          {inst.explain.why_reused.slice(0, 2).map((w, i) => (
            <p key={`ru-${i}`}>{t('sidebar.wf.reusedPrefix')}{w}</p>
          ))}
        </div>
      )}
      {inst.nodes.length > 16 && (
        <p className="text-[10px] text-[var(--theme-text-muted)]">
          {t('sidebar.wf.nodeLimited', { count: inst.nodes.length })}
        </p>
      )}
    </div>
  );
}
