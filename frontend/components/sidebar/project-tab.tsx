'use client';

import React, { useEffect, useId, useState } from 'react';
import {
  Plus,
  Layers,
  Activity,
  Database,
  Workflow as WorkflowIcon,
  ChevronRight,
  Lock,
} from 'lucide-react';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';
import { useHudStore } from '@/lib/store/useHudStore';
import { useAuthUser } from '@/lib/auth/use-auth-user';
import { useWorkflowWorkspace } from '@/lib/hooks/use-workflow-workspace';
import { formatCrs, formatOutcomeMessage, outcomeToastVariant, shortId } from '@/lib/workflow/recovery';
import { RunInspector } from './workflow/run-inspector';
import { CartoMemoryPanel } from './carto-memory-panel';
import { MapProductVersionsPanel } from './map-product-versions';

export function ProjectTab() {
  const uid = useId();
  const [newProjName, setNewProjName] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const addToast = useToastStore((s) => s.addToast);
  const ws = useWorkflowWorkspace();
  const setActiveProjectId = useHudStore((s) => s.setActiveProjectId);
  // #528：项目写路径（创建/重新运行/回放/续跑）后端要求认证（#501）。
  // 匿名默认模式下点击会裸 401 —— 参照 #469 导出门控，未登录时禁用写控件
  // 并给出可见的登录引导。
  const authUser = useAuthUser();

  // #558: 把项目 tab 的选择镜像进 HUD store —— chat 发送时据此在请求体携带
  // project_id（后端 context assembler 注入项目摘要）。workspace 级选择，
  // 不随会话切换清空（项目 tab 的 select 始终显示当前选择，请求须与 UI 一致）。
  useEffect(() => {
    setActiveProjectId(ws.selectedProjectId || null);
  }, [ws.selectedProjectId, setActiveProjectId]);

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
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-edge-subtle bg-surface-panel px-panel py-1.5">
        <span className="text-meta font-semibold text-ink-secondary">项目工作区</span>
        {ws.view === 'project' && (
          <IconButton
            label="新建项目"
            icon={Plus}
            iconSize={15}
            active={showCreate}
            disabled={!authUser}
            onClick={() => setShowCreate(!showCreate)}
          />
        )}
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-panel py-2 text-body">
        {ws.loading ? (
          <LoadingState label="加载项目…" />
        ) : (
          <>
            {ws.error && <InlineNotice variant="error">{ws.error}</InlineNotice>}

            {ws.view === 'project' && (
              <>
                {!authUser && (
                  <p className="flex items-center gap-1.5 text-caption text-ink-muted">
                    <Lock size={12} aria-hidden />
                    创建项目 / 运行工作流需要登录账号 — 请先在 设置 → 账户 登录
                  </p>
                )}
                {showCreate && (
                  <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5">
                    <label
                      htmlFor={`${uid}-project-name`}
                      className="block text-meta font-medium text-ink-secondary"
                    >
                      项目名称
                    </label>
                    <input
                      id={`${uid}-project-name`}
                      type="text"
                      placeholder="项目名称…"
                      value={newProjName}
                      onChange={(e) => setNewProjName(e.target.value)}
                      className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
                    />
                    <button
                      type="button"
                      onClick={handleCreateProject}
                      disabled={!authUser}
                      className="w-full rounded-sm bg-status-accent py-1.5 text-meta font-medium text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      创建项目
                    </button>
                  </div>
                )}

                <div>
                  <label
                    htmlFor={`${uid}-project-select`}
                    className="mb-1 block text-meta font-medium text-ink-secondary"
                  >
                    当前项目
                  </label>
                  <select
                    id={`${uid}-project-select`}
                    value={ws.selectedProjectId}
                    onChange={(e) => ws.selectProject(e.target.value)}
                    className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
                  >
                    {ws.projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.status})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-meta font-medium text-ink-secondary">
                    <span className="flex items-center gap-1.5">
                      <Layers size={14} className="text-ink-muted" aria-hidden /> 挂载数据集 ({ws.datasets.length})
                    </span>
                  </div>
                  {ws.datasets.length === 0 ? (
                    <EmptyState icon={Database} title="暂无挂载数据集" />
                  ) : (
                    <div className="space-y-1.5">
                      {ws.datasets.map((d) => (
                        <div
                          key={d.id}
                          className="flex items-center justify-between rounded-md border border-edge-subtle bg-surface-raised px-panel py-2"
                        >
                          <div className="min-w-0">
                            <div className="text-meta font-medium text-ink">{d.name}</div>
                            <div className="text-micro text-ink-muted">
                              {formatCrs(d.crs)} • {d.source_type}
                            </div>
                          </div>
                          <StatusBadge status={d.quality_status} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* ADR-0069 / spec 开放问题 2：项目制图记忆治理面板。
                    放在数据集之下——记忆与数据集同属"项目的长期状态"。 */}
                <CartoMemoryPanel projectId={ws.selectedProjectId} />

                {/* ADR-0092 A6：Map Product 版本台账 + 五维差异（版本工作区）。
                    只读真相 + 复用 rerun_from_step；仅样式变更不触发分析重算。 */}
                <MapProductVersionsPanel
                  projectId={ws.selectedProjectId}
                  onRerunStarted={(runId) => {
                    addToast(runId ? `已从分析步骤重跑（${shortId(runId, 8)}）` : '已触发重跑', 'success');
                  }}
                  onRerunError={(message) => addToast(message, 'error')}
                />

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-meta font-medium text-ink-secondary">
                    <span className="flex items-center gap-1.5">
                      <Activity size={14} className="text-ink-muted" aria-hidden /> 已保存工作流 ({ws.workflows.length})
                    </span>
                  </div>
                  {ws.workflows.length === 0 ? (
                    <EmptyState icon={WorkflowIcon} title="暂无已保存工作流" description="保存一份 Plan 即可创建" />
                  ) : (
                    <div className="space-y-2">
                      {ws.workflows.map((w) => (
                        <div
                          key={w.id}
                          className="space-y-2 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <button
                              type="button"
                              onClick={() => ws.openWorkflow(w.id)}
                              className="flex min-w-0 items-center gap-1 text-left text-meta font-medium text-ink hover:underline"
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
                              disabled={ws.actionBusy || !authUser}
                              title={authUser ? undefined : '需要登录账号（设置 → 账户）'}
                              className="border border-edge-subtle bg-surface-raised text-ink hover:bg-surface-sunken"
                            />
                          </div>
                          <div className="text-micro text-ink-muted">步骤 {w.step_count}</div>
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
                  className="text-micro text-ink-secondary hover:underline"
                >
                  ← 返回项目
                </button>
                <div>
                  <div className="text-body font-semibold text-ink">
                    {ws.selectedWorkflow.name}
                  </div>
                  <div className="text-micro text-ink-muted">
                    v{ws.selectedWorkflow.version} · {ws.selectedWorkflow.step_count} 步
                  </div>
                </div>
                <section aria-labelledby="wf-rev-heading" className="space-y-1">
                  <h3 id="wf-rev-heading" className="text-micro font-semibold uppercase tracking-wide text-ink-muted">
                    不可变修订
                  </h3>
                  {ws.revisions.length === 0 ? (
                    <p className="text-micro text-ink-muted">暂无修订</p>
                  ) : (
                    <ul className="space-y-1">
                      {ws.revisions.map((rev) => (
                        <li
                          key={rev.id}
                          className="flex justify-between gap-2 rounded-sm border border-edge-subtle px-2 py-1 font-mono text-micro text-ink-secondary"
                        >
                          <span>r{rev.revision_no}</span>
                          <span>{shortId(rev.graph_fingerprint, 10)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section aria-labelledby="wf-runs-heading" className="space-y-1">
                  <h3 id="wf-runs-heading" className="text-micro font-semibold uppercase tracking-wide text-ink-muted">
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
                            className="flex w-full items-center justify-between gap-2 rounded-md border border-edge-subtle bg-surface-raised px-2.5 py-2 text-left hover:bg-surface-sunken"
                          >
                            <span className="min-w-0">
                              <span className="block font-mono text-micro text-ink">
                                {shortId(r.id, 10)}
                              </span>
                              {r.error_message && (
                                <span className="block truncate text-micro text-status-danger">
                                  {r.error_message}
                                </span>
                              )}
                            </span>
                            <span className="flex items-center gap-1">
                              <StatusBadge status={r.status} />
                              <ChevronRight className="h-3 w-3 text-ink-muted" aria-hidden />
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
                      className="w-full rounded-sm border border-edge-subtle py-1 text-micro text-ink-secondary hover:bg-surface-sunken"
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
