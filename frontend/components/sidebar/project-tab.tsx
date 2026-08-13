'use client';

import React, { useId, useState } from 'react';
import {
  Plus,
  Layers,
  Activity,
  Database,
  Workflow as WorkflowIcon,
  ChevronRight,
} from 'lucide-react';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';
import { useWorkflowWorkspace } from '@/lib/hooks/use-workflow-workspace';
import { formatCrs, formatOutcomeMessage, outcomeToastVariant, shortId } from '@/lib/workflow/recovery';
import { RunInspector } from './workflow/run-inspector';

export function ProjectTab() {
  const uid = useId();
  const [newProjName, setNewProjName] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const addToast = useToastStore((s) => s.addToast);
  const ws = useWorkflowWorkspace();

  const handleCreateProject = async () => {
    if (!newProjName.trim()) return;
    try {
      await ws.createProject(newProjName.trim());
      setNewProjName('');
      setShowCreate(false);
    } catch (e) {
      addToast(e instanceof Error ? e.message : '创建项目失败', 'error');
    }
  };

  const handleRerunWorkflow = async (wfId: string) => {
    const result = await ws.triggerRun(wfId);
    if (result.ok) {
      if (result.applied) {
        addToast(formatOutcomeMessage('run', result.run), outcomeToastVariant(result.run.status));
      }
    } else if (result.error) {
      addToast(result.error, 'error');
    }
  };

  const handleReplay = async (mode: 'exact' | 'latest') => {
    const result = await ws.replay(mode);
    if (result.ok && result.applied) {
      addToast(formatOutcomeMessage('replay', result.run), outcomeToastVariant(result.run.status));
    }
  };

  const handleResume = async () => {
    const result = await ws.resume();
    if (result.ok && result.applied) {
      addToast(formatOutcomeMessage('resume', result.run), outcomeToastVariant(result.run.status));
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--theme-border)] px-3 py-1.5">
        <span className="text-[12px] font-semibold text-[var(--theme-text-secondary)]">项目工作区</span>
        {ws.view === 'project' && (
          <IconButton
            label="新建项目"
            icon={Plus}
            iconSize={15}
            active={showCreate}
            onClick={() => setShowCreate(!showCreate)}
          />
        )}
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-3 text-[13px]">
        {ws.loading ? (
          <LoadingState label="加载项目…" />
        ) : (
          <>
            {ws.error && <InlineNotice variant="error">{ws.error}</InlineNotice>}

            {ws.view === 'project' && (
              <>
                {showCreate && (
                  <div className="space-y-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-3">
                    <label
                      htmlFor={`${uid}-project-name`}
                      className="block text-[12px] font-medium text-[var(--theme-text-secondary)]"
                    >
                      项目名称
                    </label>
                    <input
                      id={`${uid}-project-name`}
                      type="text"
                      placeholder="项目名称…"
                      value={newProjName}
                      onChange={(e) => setNewProjName(e.target.value)}
                      className="w-full rounded border px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                      style={{
                        backgroundColor: 'var(--theme-bg-input)',
                        borderColor: 'var(--theme-border)',
                        color: 'var(--theme-text-primary)',
                      }}
                    />
                    <button
                      type="button"
                      onClick={handleCreateProject}
                      className="w-full rounded py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
                      style={{ background: 'var(--agent-accent, #16a34a)' }}
                    >
                      创建项目
                    </button>
                  </div>
                )}

                <div>
                  <label
                    htmlFor={`${uid}-project-select`}
                    className="mb-1 block text-xs font-medium text-[var(--theme-text-secondary)]"
                  >
                    当前项目
                  </label>
                  <select
                    id={`${uid}-project-select`}
                    value={ws.selectedProjectId}
                    onChange={(e) => ws.selectProject(e.target.value)}
                    className="w-full rounded border px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                    style={{
                      backgroundColor: 'var(--theme-bg-input)',
                      borderColor: 'var(--theme-border)',
                      color: 'var(--theme-text-primary)',
                    }}
                  >
                    {ws.projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.status})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs font-medium text-[var(--theme-text-secondary)]">
                    <span className="flex items-center gap-1.5">
                      <Layers className="h-3.5 w-3.5 text-emerald-500" /> 挂载数据集 ({ws.datasets.length})
                    </span>
                  </div>
                  {ws.datasets.length === 0 ? (
                    <EmptyState icon={Database} title="暂无挂载数据集" />
                  ) : (
                    <div className="space-y-1.5">
                      {ws.datasets.map((d) => (
                        <div
                          key={d.id}
                          className="flex items-center justify-between rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-2.5"
                        >
                          <div className="min-w-0">
                            <div className="text-xs font-medium text-[var(--theme-text-primary)]">{d.name}</div>
                            <div className="text-[10px] text-[var(--theme-text-muted)]">
                              {formatCrs(d.crs)} • {d.source_type}
                            </div>
                          </div>
                          <StatusBadge status={d.quality_status} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs font-medium text-[var(--theme-text-secondary)]">
                    <span className="flex items-center gap-1.5">
                      <Activity className="h-3.5 w-3.5 text-amber-500" /> 已保存工作流 ({ws.workflows.length})
                    </span>
                  </div>
                  {ws.workflows.length === 0 ? (
                    <EmptyState icon={WorkflowIcon} title="暂无已保存工作流" description="保存一份 Plan 即可创建" />
                  ) : (
                    <div className="space-y-2">
                      {ws.workflows.map((w) => (
                        <div
                          key={w.id}
                          className="space-y-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-3"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <button
                              type="button"
                              onClick={() => ws.openWorkflow(w.id)}
                              className="flex min-w-0 items-center gap-1 text-left text-xs font-medium text-[var(--theme-text-primary)] hover:underline"
                            >
                              <span className="truncate">
                                {w.name} (v{w.version})
                              </span>
                              <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />
                            </button>
                            <ConfirmAction
                              label="重新运行"
                              confirmLabel="确认重新运行？"
                              onConfirm={() => {
                                void handleRerunWorkflow(w.id);
                              }}
                              disabled={ws.actionBusy}
                              className="border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] text-[var(--theme-text-primary)] hover:bg-[var(--theme-bg-hover)] hover:text-[var(--theme-text-primary)] dark:text-[var(--theme-text-primary)]"
                            />
                          </div>
                          <div className="text-[10px] text-[var(--theme-text-muted)]">步骤 {w.step_count}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}

            {ws.view === 'workflow' && ws.selectedWorkflow && (
              <div className="space-y-3">
                <button
                  type="button"
                  onClick={ws.back}
                  className="text-[11px] text-[var(--theme-text-secondary)] hover:underline"
                >
                  ← 返回项目
                </button>
                <div>
                  <div className="text-[13px] font-semibold text-[var(--theme-text-primary)]">
                    {ws.selectedWorkflow.name}
                  </div>
                  <div className="text-[10px] text-[var(--theme-text-muted)]">
                    v{ws.selectedWorkflow.version} · {ws.selectedWorkflow.step_count} 步
                  </div>
                </div>
                <section aria-labelledby="wf-rev-heading" className="space-y-1">
                  <h3 id="wf-rev-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
                    不可变修订
                  </h3>
                  {ws.revisions.length === 0 ? (
                    <p className="text-[11px] text-[var(--theme-text-muted)]">暂无修订</p>
                  ) : (
                    <ul className="space-y-1">
                      {ws.revisions.map((rev) => (
                        <li
                          key={rev.id}
                          className="flex justify-between gap-2 rounded border border-[var(--theme-border)] px-2 py-1 font-mono text-[10px] text-[var(--theme-text-secondary)]"
                        >
                          <span>r{rev.revision_no}</span>
                          <span>{shortId(rev.graph_fingerprint, 10)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section aria-labelledby="wf-runs-heading" className="space-y-1">
                  <h3 id="wf-runs-heading" className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">
                    运行
                  </h3>
                  {ws.detailLoading && ws.runs.length === 0 ? (
                    <LoadingState label="加载运行…" />
                  ) : ws.runs.length === 0 ? (
                    <EmptyState icon={Activity} title="暂无运行" />
                  ) : (
                    <ul className="space-y-1.5">
                      {ws.runs.map((r) => (
                        <li key={r.id}>
                          <button
                            type="button"
                            onClick={() => ws.openRun(r.id)}
                            className="flex w-full items-center justify-between gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-2.5 py-2 text-left hover:bg-[var(--theme-bg-hover)]"
                          >
                            <span className="min-w-0">
                              <span className="block font-mono text-[11px] text-[var(--theme-text-primary)]">
                                {shortId(r.id, 10)}
                              </span>
                              {r.error_message && (
                                <span className="block truncate text-[10px] text-red-600 dark:text-red-300">
                                  {r.error_message}
                                </span>
                              )}
                            </span>
                            <span className="flex items-center gap-1">
                              <StatusBadge status={r.status} />
                              <ChevronRight className="h-3 w-3 text-[var(--theme-text-muted)]" aria-hidden />
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {ws.runsHasMore && (
                    <button
                      type="button"
                      onClick={() => {
                        void ws.loadMoreRuns();
                      }}
                      className="w-full rounded border border-[var(--theme-border)] py-1 text-[11px] text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-hover)]"
                    >
                      加载更多运行
                    </button>
                  )}
                </section>
              </div>
            )}

            {(ws.view === 'run' || ws.view === 'compare') && (
              <RunInspector
                workflow={ws.selectedWorkflow}
                run={ws.runDetail}
                runs={ws.runs}
                loading={ws.detailLoading}
                actionBusy={ws.actionBusy}
                actionError={ws.actionError}
                compare={ws.compare}
                compareError={ws.compareError}
                compareBusy={ws.compareBusy}
                comparePeerId={ws.comparePeerId}
                onComparePeerChange={ws.setComparePeerId}
                onCompare={() => {
                  void ws.openCompare();
                }}
                lineageByArtifact={ws.lineageByArtifact}
                onLoadLineage={(id) => {
                  void ws.loadLineage(id);
                }}
                onBack={ws.back}
                onReplay={(mode) => {
                  void handleReplay(mode);
                }}
                onResume={() => {
                  void handleResume();
                }}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default ProjectTab;
