'use client';

/**
 * ModelOpsTab — 模型注册表浏览 + 本会话推理运行（V9，ADR-0145）。
 *
 * 后端事实（frontend/docs/knowledge-market-recon.md §3）：
 * - 注册表数据经 executeToolDirect 驱动 modelops_list_models /
 *   modelops_inspect_model / modelops_model_history（无专用 HTTP 路由）；
 * - #1212：模型经工具关键词发现，投影链无能力来源字段 —— 面板固定说明，
 *   provenance 原文展示，不美化；
 * - 运行历史无持久化端点 → 仅显示本会话观察（use-modelops-runs），空态
 *   如实说明（协调点）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Boxes, RefreshCw } from 'lucide-react';
import EmptyState from '@/components/shared/empty-state';
import { describeApiError } from '@/lib/api/transport';
import { listModelopsModels, type ModelOpsListItem } from '@/lib/api/modelops';
import { useModelopsRuns, type ModelOpsSessionRun } from '@/lib/hooks/use-modelops-runs';
import { ModelOpsModelDetail } from './modelops-model-detail';

interface ModelOpsTabProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

type ListState =
  | { status: 'loading' }
  | { status: 'ready'; models: ModelOpsListItem[] }
  | { status: 'error'; message: string };

function formatDuration(startedAt: number | null, completedAt: number | null): string {
  if (startedAt == null) return '—';
  const end = completedAt ?? Date.now();
  const ms = Math.max(end - startedAt, 0);
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function RunStatusChip({ run }: { run: ModelOpsSessionRun }) {
  const tone =
    run.status === 'completed'
      ? 'text-status-success'
      : run.status === 'failed'
        ? 'text-status-critical'
        : 'text-status-warning';
  return <span className={`shrink-0 text-meta font-medium ${tone}`}>{run.status}</span>;
}

function RunRow({ run }: { run: ModelOpsSessionRun }) {
  const [expanded, setExpanded] = useState(false);
  const outputRoles = Object.keys(run.outputs);
  return (
    <li className="rounded-md border border-edge-subtle bg-surface-raised px-3 py-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="w-full text-left"
      >
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-meta font-medium text-ink">
            {run.runId ?? run.callId}
          </span>
          <RunStatusChip run={run} />
        </div>
        <div className="mt-0.5 text-meta text-ink-muted">
          {run.modelId ?? '未知模型'}
          {run.taskType ? ` · ${run.taskType}` : ''}
          {run.reused === true ? ' · 复用产物' : ''}
          {` · ${formatDuration(run.startedAt, run.completedAt)}`}
        </div>
      </button>
      {expanded && (
        <div className="mt-2 flex flex-col gap-1.5 border-t border-edge-subtle pt-2 text-meta text-ink-secondary">
          {run.status === 'failed' && run.error && (
            <p className="text-status-critical" role="alert">
              {run.error}
            </p>
          )}
          {outputRoles.length > 0 ? (
            <ul className="flex flex-col gap-0.5">
              {Object.entries(run.outputs).map(([role, out]) => (
                <li key={role} className="min-w-0 break-all">
                  <span className="font-medium text-ink">{role}</span>
                  {typeof out.path === 'string' ? `：${out.path}` : ''}
                  {typeof out.data_object_id === 'string'
                    ? `（DataObject ${out.data_object_id}）`
                    : ''}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-ink-muted">{run.status === 'running' ? '运行中，尚无产物。' : '无产物信息。'}</p>
          )}
          {run.performance && Object.keys(run.performance).length > 0 && (
            <p className="break-all text-ink-muted">
              performance：{JSON.stringify(run.performance)}
            </p>
          )}
          <p className="text-ink-muted">
            栅格产物为 lakehouse COG DataObject（发布图层后在图层标签页查看）；
            表格产物写入 PostGIS（无独立预览端点）。
          </p>
        </div>
      )}
    </li>
  );
}

export function ModelOpsTab(_props: ModelOpsTabProps) {
  const [list, setList] = useState<ListState>({ status: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const seqRef = useRef(0);
  const mountedRef = useRef(true);
  const runs = useModelopsRuns();

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      seqRef.current += 1;
    };
  }, []);

  const refresh = useCallback(async () => {
    const seq = ++seqRef.current;
    setList({ status: 'loading' });
    try {
      const res = await listModelopsModels();
      if (!mountedRef.current || seq !== seqRef.current) return;
      setList({ status: 'ready', models: res.models ?? [] });
    } catch (err) {
      if (!mountedRef.current || seq !== seqRef.current) return;
      setList({ status: 'error', message: describeApiError(err, '无法加载模型注册表') });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (selected) {
    return <ModelOpsModelDetail modelId={selected} onBack={() => setSelected(null)} />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      <div className="flex items-center justify-between">
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold">
          ModelOps
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          aria-label="刷新模型注册表"
          disabled={list.status === 'loading'}
          className="inline-flex items-center gap-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          <RefreshCw size={12} aria-hidden className={list.status === 'loading' ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <div
        data-state="honest-capability-note"
        className="rounded-md border border-edge-subtle bg-surface-sunken/60 px-3 py-2"
      >
        <p className="text-meta leading-relaxed text-ink-secondary">
          #1212 现状：模型经由工具关键词发现，Intent→capability→model 的能力投影链
          尚未把 Model 注册为一等实体。本面板如实展示注册表与模型描述符，不推导、
          不美化能力覆盖。
        </p>
      </div>

      {list.status === 'loading' && (
        <p className="py-4 text-center text-body text-ink-muted italic">加载中…</p>
      )}
      {list.status === 'error' && (
        <p role="alert" className="text-body font-medium text-status-critical">
          {list.message}
        </p>
      )}
      {list.status === 'ready' && list.models.length === 0 && (
        <EmptyState
          icon={Boxes}
          title="注册表中暂无模型"
          description="模型注册表为空（或当前凭据 scope 下不可见）。模型经 agent 工具链注册后出现在这里。"
        />
      )}
      {list.status === 'ready' && list.models.length > 0 && (
        <ul className="flex flex-col gap-2">
          {list.models.map((m) => (
            <li key={`${m.model_id}@${m.model_version}`}>
              <button
                type="button"
                onClick={() => setSelected(m.model_id)}
                className="w-full rounded-md border border-edge-subtle bg-surface-raised px-3 py-2 text-left transition-colors hover:bg-surface-hover"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">
                    {m.model_id}
                  </span>
                  <span className="shrink-0 text-meta text-ink-muted">v{m.model_version}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1 text-meta text-ink-muted">
                  {(m.task_types ?? []).map((t) => (
                    <span key={t} className="rounded-sm bg-surface-sunken px-1.5 py-0.5">
                      {t}
                    </span>
                  ))}
                </div>
                <div className="mt-1 text-meta text-ink-muted">
                  {m.provider_type} · {m.license} · checksum {m.checksum}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* 推理运行（本会话观察） */}
      <div className="mt-2">
        <div className="mb-1.5 text-heading uppercase tracking-wider text-ink-muted font-semibold">
          推理运行（本会话）
        </div>
        <p className="mb-2 text-meta leading-relaxed text-ink-muted">
          后端未提供持久化运行历史查询端点（run_id 仅取消可用）—— 以下仅为本会话
          chat 工具事件中观察到的推理调用，刷新后不保留（协调点）。
        </p>
        {runs.length === 0 ? (
          <p className="text-body text-ink-muted italic">
            本会话暂无推理调用 —— 通过对话发起模型推理后，run 会出现在这里。
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {runs.map((run) => (
              <RunRow key={run.callId} run={run} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export default ModelOpsTab;
