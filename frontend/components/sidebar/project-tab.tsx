'use client';

import React, { useId, useState, useEffect } from 'react';
import {
  Play,
  Plus,
  Layers,
  Activity,
  Database,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import {
  Project,
  ProjectDataset,
  Workflow,
  fetchProjects,
  createProject,
  fetchProjectDatasets,
  fetchProjectWorkflows,
  runWorkflow
} from '@/lib/api/project';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';

export function ProjectTab() {
  const uid = useId();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [datasets, setDatasets] = useState<ProjectDataset[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [newProjName, setNewProjName] = useState<string>("");
  const [showCreate, setShowCreate] = useState<boolean>(false);
  const addToast = useToastStore((s) => s.addToast);

  // Load once on mount; loadProjects is a stable component function.
  useEffect(() => {
    loadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedProjectId) {
      loadProjectDetails(selectedProjectId);
    }
  }, [selectedProjectId]);

  const loadProjects = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchProjects();
      setProjects(data);
      if (data.length > 0 && !selectedProjectId) {
        setSelectedProjectId(data[0].id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载项目列表失败");
    } finally {
      setLoading(false);
    }
  };

  const loadProjectDetails = async (projId: string) => {
    // Review P2 修复：成功路径要清掉上一次失败的残留错误横幅。
    setError(null);
    try {
      const [ds, wf] = await Promise.all([
        fetchProjectDatasets(projId),
        fetchProjectWorkflows(projId)
      ]);
      setDatasets(ds);
      setWorkflows(wf);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载项目详情失败");
    }
  };

  const handleCreateProject = async () => {
    if (!newProjName.trim()) return;
    try {
      const proj = await createProject(newProjName);
      setProjects([proj, ...projects]);
      setSelectedProjectId(proj.id);
      setNewProjName("");
      setShowCreate(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建项目失败");
    }
  };

  const handleRerunWorkflow = async (wfId: string) => {
    if (!selectedProjectId) return;
    try {
      await runWorkflow(selectedProjectId, wfId);
      addToast("工作流已成功触发重新运行", "success");
      loadProjectDetails(selectedProjectId);
    } catch (e) {
      addToast(`重新运行失败：${e instanceof Error ? e.message : "未知错误"}`, "error");
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 细工具栏：标题 + 新建项目（面板头部由 PanelHeader 提供） */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--theme-border)] px-3 py-1.5">
        <span className="text-[12px] font-semibold text-[var(--theme-text-secondary)]">项目工作区</span>
        <IconButton
          label="新建项目"
          icon={Plus}
          iconSize={15}
          active={showCreate}
          onClick={() => setShowCreate(!showCreate)}
        />
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-3 text-[13px]">
        {loading ? (
          <LoadingState label="加载项目…" />
        ) : (
          <>
            {error && <InlineNotice variant="error">{error}</InlineNotice>}

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
                    backgroundColor: "var(--theme-bg-input)",
                    borderColor: "var(--theme-border)",
                    color: "var(--theme-text-primary)",
                  }}
                />
                <button
                  type="button"
                  onClick={handleCreateProject}
                  className="w-full rounded py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
                  style={{ background: "var(--agent-accent, #16a34a)" }}
                >
                  创建项目
                </button>
              </div>
            )}

            {/* Project Selector */}
            <div>
              <label
                htmlFor={`${uid}-project-select`}
                className="mb-1 block text-xs font-medium text-[var(--theme-text-secondary)]"
              >
                当前项目
              </label>
              <select
                id={`${uid}-project-select`}
                value={selectedProjectId}
                onChange={(e) => setSelectedProjectId(e.target.value)}
                className="w-full rounded border px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                style={{
                  backgroundColor: "var(--theme-bg-input)",
                  borderColor: "var(--theme-border)",
                  color: "var(--theme-text-primary)",
                }}
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.status})
                  </option>
                ))}
              </select>
            </div>

            {/* Datasets Section */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-medium text-[var(--theme-text-secondary)]">
                <span className="flex items-center gap-1.5">
                  <Layers className="h-3.5 w-3.5 text-emerald-500" /> 挂载数据集 ({datasets.length})
                </span>
              </div>
              {datasets.length === 0 ? (
                <EmptyState icon={Database} title="暂无挂载数据集" />
              ) : (
                <div className="space-y-1.5">
                  {datasets.map((d) => (
                    <div
                      key={d.id}
                      className="flex items-center justify-between rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-2.5"
                    >
                      <div>
                        <div className="text-xs font-medium text-[var(--theme-text-primary)]">{d.name}</div>
                        <div className="text-[10px] text-[var(--theme-text-muted)]">{d.crs} • {d.source_type}</div>
                      </div>
                      {/* 质量状态复用 StatusBadge（未知值回退原始文案） */}
                      <StatusBadge status={d.quality_status} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 已保存工作流区块 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-medium text-[var(--theme-text-secondary)]">
                <span className="flex items-center gap-1.5">
                  <Activity className="h-3.5 w-3.5 text-amber-500" /> 已保存工作流 ({workflows.length})
                </span>
              </div>
              {workflows.length === 0 ? (
                <EmptyState icon={WorkflowIcon} title="暂无已保存工作流" description="保存一份 Plan 即可创建" />
              ) : (
                <div className="space-y-2">
                  {workflows.map((w) => (
                    <div
                      key={w.id}
                      className="space-y-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] p-3"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-[var(--theme-text-primary)]">{w.name} (v{w.version})</span>
                        <button
                          type="button"
                          onClick={() => handleRerunWorkflow(w.id)}
                          className="flex items-center gap-1 rounded px-2 py-1 text-[10px] font-medium text-white transition-opacity hover:opacity-85"
                          style={{ background: "var(--agent-accent, #16a34a)" }}
                        >
                          <Play className="h-3 w-3" /> 重新运行
                        </button>
                      </div>
                      <div className="text-[10px] text-[var(--theme-text-muted)]">
                        步骤 ({w.graph_spec.steps?.length || 0})：{w.graph_spec.steps?.map((s) => s.tool_name).join(" → ")}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default ProjectTab;
