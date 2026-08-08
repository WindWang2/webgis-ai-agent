"use client";

import React, { useState, useEffect } from "react";
import {
  Folder,
  Play,
  CheckCircle,
  AlertTriangle,
  XCircle,
  Plus,
  RefreshCw,
  Layers,
  Activity,
  FileCheck
} from "lucide-react";
import {
  Project,
  ProjectDataset,
  Workflow,
  WorkflowRun,
  fetchProjects,
  createProject,
  fetchProjectDatasets,
  fetchProjectWorkflows,
  runWorkflow
} from "@/lib/api/project";

export function ProjectTab() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [datasets, setDatasets] = useState<ProjectDataset[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [newProjName, setNewProjName] = useState<string>("");
  const [showCreate, setShowCreate] = useState<boolean>(false);

  useEffect(() => {
    loadProjects();
  }, []);

  useEffect(() => {
    if (selectedProjectId) {
      loadProjectDetails(selectedProjectId);
    }
  }, [selectedProjectId]);

  const loadProjects = async () => {
    try {
      setLoading(true);
      const data = await fetchProjects();
      setProjects(data);
      if (data.length > 0 && !selectedProjectId) {
        setSelectedProjectId(data[0].id);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const loadProjectDetails = async (projId: string) => {
    try {
      const [ds, wf] = await Promise.all([
        fetchProjectDatasets(projId),
        fetchProjectWorkflows(projId)
      ]);
      setDatasets(ds);
      setWorkflows(wf);
    } catch (e) {
      console.error(e);
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
      console.error(e);
    }
  };

  const handleRerunWorkflow = async (wfId: string) => {
    if (!selectedProjectId) return;
    try {
      await runWorkflow(selectedProjectId, wfId);
      alert("Workflow Re-run triggered successfully!");
      loadProjectDetails(selectedProjectId);
    } catch (e: any) {
      alert("Re-run failed: " + e.message);
    }
  };

  return (
    <div className="p-4 space-y-6 text-sm text-slate-200 h-full overflow-y-auto">
      <div className="flex items-center justify-between border-b border-slate-700/60 pb-3">
        <div className="flex items-center gap-2 font-semibold text-base text-slate-100">
          <Folder className="w-5 h-5 text-indigo-400" />
          <span>Project Workspace</span>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="p-1 text-slate-400 hover:text-indigo-400 rounded transition"
          title="New Project"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>

      {showCreate && (
        <div className="bg-slate-800/80 p-3 rounded border border-slate-700 space-y-2">
          <input
            type="text"
            placeholder="Project Name..."
            value={newProjName}
            onChange={(e) => setNewProjName(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
          />
          <button
            onClick={handleCreateProject}
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium py-1.5 rounded transition"
          >
            Create Project
          </button>
        </div>
      )}

      {/* Project Selector */}
      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1">Active Project</label>
        <select
          value={selectedProjectId}
          onChange={(e) => setSelectedProjectId(e.target.value)}
          className="w-full bg-slate-800 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
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
        <div className="flex items-center justify-between text-xs font-medium text-slate-400">
          <span className="flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-emerald-400" /> Attached Datasets ({datasets.length})
          </span>
        </div>
        {datasets.length === 0 ? (
          <div className="text-xs text-slate-500 italic p-2 bg-slate-800/40 rounded">No attached datasets.</div>
        ) : (
          <div className="space-y-1.5">
            {datasets.map((d) => (
              <div key={d.id} className="bg-slate-800/60 p-2.5 rounded border border-slate-700/50 flex items-center justify-between">
                <div>
                  <div className="font-medium text-slate-200 text-xs">{d.name}</div>
                  <div className="text-[10px] text-slate-400">{d.crs} • {d.source_type}</div>
                </div>
                <div className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <CheckCircle className="w-3 h-3" /> {d.quality_status}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Persistent Workflows Section */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs font-medium text-slate-400">
          <span className="flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5 text-amber-400" /> Persistent Workflows ({workflows.length})
          </span>
        </div>
        {workflows.length === 0 ? (
          <div className="text-xs text-slate-500 italic p-2 bg-slate-800/40 rounded">No saved workflows. Save a Plan to create one.</div>
        ) : (
          <div className="space-y-2">
            {workflows.map((w) => (
              <div key={w.id} className="bg-slate-800/60 p-3 rounded border border-slate-700/50 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-slate-200 text-xs">{w.name} (v{w.version})</span>
                  <button
                    onClick={() => handleRerunWorkflow(w.id)}
                    className="flex items-center gap-1 text-[10px] bg-indigo-600/80 hover:bg-indigo-500 text-white px-2 py-1 rounded transition"
                  >
                    <Play className="w-3 h-3" /> Re-run
                  </button>
                </div>
                <div className="text-[10px] text-slate-400">
                  Steps ({w.graph_spec.steps?.length || 0}): {w.graph_spec.steps?.map((s) => s.tool_name).join(" → ")}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
