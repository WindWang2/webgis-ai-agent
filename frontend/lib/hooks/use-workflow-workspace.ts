/**
 * Project / workflow / run workspace data.
 *
 * Guards:
 *   - AbortController per project / workflow / run switch
 *   - monotonic generation so a stale response cannot overwrite the current view
 *   - action lock so replay/resume cannot double-submit
 *   - bounded poll only while the selected run is pending/running
 *   - lineage / compare fetched only when requested
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  createProject as apiCreateProject,
  fetchArtifactLineage,
  fetchProjectDatasets,
  fetchProjectWorkflows,
  fetchProjects,
  fetchRunComparison,
  fetchWorkflowRevisions,
  fetchWorkflowRun,
  fetchWorkflowRuns,
  invalidateProjectRunCaches,
  replayWorkflowRun,
  resumeWorkflowRun,
  runWorkflow,
  type LineageGraph,
  type Project,
  type ProjectDataset,
  type ReplayMode,
  type RunComparison,
  type WorkflowRevisionSummary,
  type WorkflowRunDetail,
  type WorkflowRunSummary,
  type WorkflowSummary,
} from '@/lib/api/project';
import { ApiTimeoutError } from '@/lib/api/transport';
import { isAbortError, isActiveRunStatus, parseApiErrorDetail } from '@/lib/workflow/recovery';

export const RUN_POLL_INTERVAL_MS = 3000;
export const RUN_POLL_MAX = 40;

export type WorkspaceView = 'project' | 'workflow' | 'run' | 'compare';

export interface UseWorkflowWorkspaceResult {
  projects: Project[];
  selectedProjectId: string;
  selectProject: (id: string) => void;
  createProject: (name: string) => Promise<void>;
  datasets: ProjectDataset[];
  workflows: WorkflowSummary[];
  revisions: WorkflowRevisionSummary[];
  runs: WorkflowRunSummary[];
  runsHasMore: boolean;
  loadMoreRuns: () => Promise<void>;
  selectedWorkflow: WorkflowSummary | null;
  selectedRunId: string;
  runDetail: WorkflowRunDetail | null;
  view: WorkspaceView;
  loading: boolean;
  detailLoading: boolean;
  error: string | null;
  actionError: string | null;
  actionBusy: boolean;
  compare: RunComparison | null;
  compareError: string | null;
  compareBusy: boolean;
  comparePeerId: string;
  setComparePeerId: (id: string) => void;
  lineageByArtifact: Record<string, LineageGraph | 'loading' | 'empty' | 'error'>;
  loadLineage: (artifactId: string) => Promise<void>;
  openWorkflow: (id: string) => void;
  openRun: (id: string) => void;
  back: () => void;
  openCompare: () => Promise<void>;
  triggerRun: (workflowId: string) => Promise<ActionResult>;
  replay: (mode: ReplayMode) => Promise<ActionResult>;
  resume: () => Promise<ActionResult>;
}

export type ActionResult =
  | { ok: true; run: WorkflowRunDetail; applied: boolean }
  | { ok: false; error: string }
  | { ok: false; error: null };

export function useWorkflowWorkspace(): UseWorkflowWorkspaceResult {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [datasets, setDatasets] = useState<ProjectDataset[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [revisions, setRevisions] = useState<WorkflowRevisionSummary[]>([]);
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [runsHasMore, setRunsHasMore] = useState(false);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [runDetail, setRunDetail] = useState<WorkflowRunDetail | null>(null);
  const [view, setView] = useState<WorkspaceView>('project');
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [compare, setCompare] = useState<RunComparison | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const [comparePeerId, setComparePeerId] = useState('');
  // 轮询自续期：status 不变时 effect 不会重跑，必须靠 tick 变化排下一次
  // （同 use-job-center 的模式），否则只轮询一次就停在 "running"。
  const [pollTick, setPollTick] = useState(0);
  const [lineageByArtifact, setLineageByArtifact] = useState<
    Record<string, LineageGraph | 'loading' | 'empty' | 'error'>
  >({});

  const listGen = useRef(0);
  const detailGen = useRef(0);
  const workflowGen = useRef(0);
  const runGen = useRef(0);
  const projectAbort = useRef<AbortController | null>(null);
  const workflowAbort = useRef<AbortController | null>(null);
  const runAbort = useRef<AbortController | null>(null);
  const lineageAbort = useRef<AbortController | null>(null);
  const compareAbort = useRef<AbortController | null>(null);
  const actionLock = useRef(false);
  const actionEpoch = useRef(0);
  const selectedProjectIdRef = useRef('');
  const selectedWorkflowIdRef = useRef('');
  const selectedRunIdRef = useRef('');
  const comparePeerIdRef = useRef('');
  const pollCount = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);

  selectedProjectIdRef.current = selectedProjectId;
  selectedWorkflowIdRef.current = selectedWorkflowId;
  selectedRunIdRef.current = selectedRunId;
  comparePeerIdRef.current = comparePeerId;

  const selectedWorkflow = workflows.find((w) => w.id === selectedWorkflowId) ?? null;

  const selectProject = useCallback((id: string) => {
    detailGen.current += 1;
    workflowGen.current += 1;
    runGen.current += 1;
    actionEpoch.current += 1;
    setSelectedProjectId(id);
    setSelectedWorkflowId('');
    setSelectedRunId('');
    setRunDetail(null);
    setRevisions([]);
    setRuns([]);
    setCompare(null);
    setCompareError(null);
    setComparePeerId('');
    setLineageByArtifact({});
    setActionError(null);
    setView('project');
  }, []);

  useEffect(() => {
    mounted.current = true;
    const ac = new AbortController();
    const gen = ++listGen.current;
    setLoading(true);
    setError(null);
    void fetchProjects({ signal: ac.signal })
      .then((list) => {
        if (gen !== listGen.current) return;
        setProjects(list);
        setSelectedProjectId((cur) => cur || list[0]?.id || '');
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted || isAbortError(err) || gen !== listGen.current) return;
        setError(parseApiErrorDetail(err, '加载项目列表失败'));
      })
      .finally(() => {
        if (gen === listGen.current) setLoading(false);
      });
    return () => {
      ac.abort();
    };
  }, []);

  useEffect(() => {
    if (!selectedProjectId) {
      setDatasets([]);
      setWorkflows([]);
      return;
    }
    projectAbort.current?.abort();
    const ac = new AbortController();
    projectAbort.current = ac;
    const gen = ++detailGen.current;
    setError(null);
    void Promise.all([
      fetchProjectDatasets(selectedProjectId, { signal: ac.signal }),
      fetchProjectWorkflows(selectedProjectId, { signal: ac.signal }),
    ])
      .then(([ds, wf]) => {
        if (gen !== detailGen.current) return;
        setDatasets(ds);
        setWorkflows(wf);
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted || isAbortError(err) || gen !== detailGen.current) return;
        setError(parseApiErrorDetail(err, '加载项目详情失败'));
      });
    return () => ac.abort();
  }, [selectedProjectId]);

  const openWorkflow = useCallback(
    (id: string) => {
      setSelectedWorkflowId(id);
      setSelectedRunId('');
      setRunDetail(null);
      setCompare(null);
      setComparePeerId('');
      setLineageByArtifact({});
      setActionError(null);
      setView('workflow');
    },
    [],
  );

  useEffect(() => {
    if (!selectedProjectId || !selectedWorkflowId) return;
    workflowAbort.current?.abort();
    const ac = new AbortController();
    workflowAbort.current = ac;
    const gen = ++workflowGen.current;
    setDetailLoading(true);
    void Promise.all([
      fetchWorkflowRuns(selectedProjectId, { workflowId: selectedWorkflowId, signal: ac.signal }),
      fetchWorkflowRevisions(selectedProjectId, selectedWorkflowId, { signal: ac.signal }),
    ])
      .then(([runPage, revPage]) => {
        if (!mounted.current || gen !== workflowGen.current) return;
        setRuns(runPage.items);
        setRunsHasMore(runPage.has_more);
        setRevisions(revPage.items);
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted || isAbortError(err) || gen !== workflowGen.current) return;
        setError(parseApiErrorDetail(err, '加载运行列表失败'));
      })
      .finally(() => {
        if (gen === workflowGen.current && mounted.current) setDetailLoading(false);
      });
    return () => ac.abort();
  }, [selectedProjectId, selectedWorkflowId]);

  const openRun = useCallback((id: string) => {
    lineageAbort.current?.abort();
    compareAbort.current?.abort();
    setSelectedRunId(id);
    setCompare(null);
    setCompareError(null);
    setLineageByArtifact({});
    setActionError(null);
    setView('run');
  }, []);

  const loadRunDetail = useCallback(
    async (projectId: string, runId: string, signal: AbortSignal, gen: number) => {
      const detail = await fetchWorkflowRun(projectId, runId, { forceRefresh: true, signal });
      if (!mounted.current || gen !== runGen.current) return null;
      setRunDetail(detail);
      return detail;
    },
    [],
  );

  useEffect(() => {
    if (!selectedProjectId || !selectedRunId) {
      setRunDetail(null);
      return;
    }
    runAbort.current?.abort();
    const ac = new AbortController();
    runAbort.current = ac;
    const gen = ++runGen.current;
    pollCount.current = 0;
    setDetailLoading(true);
    void loadRunDetail(selectedProjectId, selectedRunId, ac.signal, gen)
      .catch((err: unknown) => {
        if (ac.signal.aborted || isAbortError(err) || gen !== runGen.current) return;
        setError(parseApiErrorDetail(err, '加载运行详情失败'));
      })
      .finally(() => {
        if (gen === runGen.current && mounted.current) setDetailLoading(false);
      });
    return () => ac.abort();
  }, [selectedProjectId, selectedRunId, loadRunDetail]);

  useEffect(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
    if (!selectedProjectId || !selectedRunId) return;
    if (!isActiveRunStatus(runDetail?.status)) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    if (pollCount.current >= RUN_POLL_MAX) return;

    pollTimer.current = setTimeout(() => {
      pollCount.current += 1;
      const ac = new AbortController();
      runAbort.current = ac;
      const gen = runGen.current;
      void loadRunDetail(selectedProjectId, selectedRunId, ac.signal, gen)
        .catch((err: unknown) => {
          if (ac.signal.aborted || isAbortError(err) || gen !== runGen.current) return;
          setError(parseApiErrorDetail(err, '刷新运行状态失败'));
        })
        .finally(() => {
          setPollTick((tick) => tick + 1);
        });
    }, RUN_POLL_INTERVAL_MS);

    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [selectedProjectId, selectedRunId, runDetail?.status, pollTick, loadRunDetail]);

  const back = useCallback(() => {
    setActionError(null);
    if (view === 'compare') {
      setView('run');
      setCompare(null);
      return;
    }
    if (view === 'run') {
      setView('workflow');
      setSelectedRunId('');
      setRunDetail(null);
      return;
    }
    setView('project');
    setSelectedWorkflowId('');
    setRuns([]);
    setRevisions([]);
  }, [view]);

  const createProject = useCallback(async (name: string) => {
    const proj = await apiCreateProject(name);
    if (!mounted.current) return;
    setProjects((prev) => [proj, ...prev]);
    selectProject(proj.id);
  }, [selectProject]);

  const loadLineage = useCallback(
    async (artifactId: string) => {
      if (!artifactId) return;
      const existing = lineageByArtifact[artifactId];
      if (existing && existing !== 'error') return;
      lineageAbort.current?.abort();
      const ac = new AbortController();
      lineageAbort.current = ac;
      const gen = runGen.current;
      const projectId = selectedProjectId;
      const runId = selectedRunId;
      setLineageByArtifact((prev) => ({ ...prev, [artifactId]: 'loading' }));
      try {
        const graph = await fetchArtifactLineage(artifactId, { signal: ac.signal });
        if (!mounted.current || gen !== runGen.current || selectedProjectId !== projectId || selectedRunId !== runId) {
          return;
        }
        const empty = (graph.parents?.length ?? 0) === 0 && (graph.consumers?.length ?? 0) === 0;
        setLineageByArtifact((prev) => ({ ...prev, [artifactId]: empty ? 'empty' : graph }));
      } catch (err: unknown) {
        if (ac.signal.aborted || isAbortError(err) || gen !== runGen.current) return;
        setLineageByArtifact((prev) => ({ ...prev, [artifactId]: 'error' }));
      }
    },
    [lineageByArtifact, selectedProjectId, selectedRunId],
  );

  const openCompare = useCallback(async () => {
    if (!selectedProjectId || !selectedRunId || !comparePeerId) return;
    if (comparePeerId === selectedRunId) {
      setCompareError('请选择另一次运行进行对比');
      return;
    }
    compareAbort.current?.abort();
    const ac = new AbortController();
    compareAbort.current = ac;
    const gen = runGen.current;
    const requestedA = selectedRunId;
    const requestedB = comparePeerId;
    setCompareError(null);
    setCompareBusy(true);
    try {
      const result = await fetchRunComparison(selectedProjectId, selectedRunId, comparePeerId, {
        signal: ac.signal,
      });
      if (!mounted.current || gen !== runGen.current) return;
      if (selectedRunIdRef.current !== requestedA || comparePeerIdRef.current !== requestedB) return;
      setCompare(result);
      setView('compare');
    } catch (err: unknown) {
      if (ac.signal.aborted || isAbortError(err) || gen !== runGen.current) return;
      setCompareError(parseApiErrorDetail(err, '对比失败'));
    } finally {
      if (gen === runGen.current && mounted.current) setCompareBusy(false);
    }
  }, [selectedProjectId, selectedRunId, comparePeerId]);

  const loadMoreRuns = useCallback(async () => {
    if (!selectedProjectId || !selectedWorkflowId || !runsHasMore) return;
    const gen = workflowGen.current;
    const offset = runs.length;
    try {
      const page = await fetchWorkflowRuns(selectedProjectId, {
        workflowId: selectedWorkflowId,
        offset,
        forceRefresh: true,
      });
      if (gen !== workflowGen.current) return;
      setRuns((prev) => {
        const seen = new Set(prev.map((r) => r.id));
        return [...prev, ...page.items.filter((r) => !seen.has(r.id))];
      });
      setRunsHasMore(page.has_more);
    } catch (err: unknown) {
      if (isAbortError(err) || gen !== workflowGen.current) return;
      setError(parseApiErrorDetail(err, '加载更多运行失败'));
    }
  }, [selectedProjectId, selectedWorkflowId, runsHasMore, runs.length]);

  const refreshRunsIfCurrent = useCallback(async (projectId: string, workflowId: string, epoch: number) => {
    if (!projectId || !workflowId) return;
    try {
      const page = await fetchWorkflowRuns(projectId, {
        workflowId,
        forceRefresh: true,
      });
      if (!mounted.current || epoch !== actionEpoch.current) return;
      if (selectedProjectIdRef.current !== projectId) return;
      setRuns(page.items);
      setRunsHasMore(page.has_more);
    } catch {
      /* list refresh is best-effort after a mutation */
    }
  }, []);

  const withActionLock = useCallback(
    async (fn: () => Promise<WorkflowRunDetail>): Promise<ActionResult> => {
      if (actionLock.current) return { ok: false, error: null };
      actionLock.current = true;
      setActionBusy(true);
      setActionError(null);
      const epoch = actionEpoch.current;
      const sourceProjectId = selectedProjectIdRef.current;
      const sourceWorkflowId = selectedWorkflowIdRef.current;
      const sourceRunId = selectedRunIdRef.current;
      try {
        const result = await fn();
        if (!mounted.current) return { ok: true, run: result, applied: false };
        const stillSameProject =
          epoch === actionEpoch.current && selectedProjectIdRef.current === sourceProjectId;
        const stillSameWorkflow = selectedWorkflowIdRef.current === sourceWorkflowId;
        const applied = stillSameProject && selectedRunIdRef.current === sourceRunId;
        if (applied) {
          setRunDetail(result);
          setSelectedRunId(result.id);
          setView('run');
        }
        if (stillSameProject && stillSameWorkflow) {
          await refreshRunsIfCurrent(sourceProjectId, sourceWorkflowId, epoch);
        }
        return { ok: true, run: result, applied };
      } catch (err: unknown) {
        if (sourceProjectId) invalidateProjectRunCaches(sourceProjectId);
        if (!mounted.current) return { ok: false, error: null };
        if (isAbortError(err)) return { ok: false, error: null };
        const timedOut = err instanceof ApiTimeoutError;
        const message = timedOut
          ? '请求超时，已刷新运行列表（后端可能已创建新运行）'
          : parseApiErrorDetail(err, '操作被后端拒绝');
        const stillSameProject =
          epoch === actionEpoch.current && selectedProjectIdRef.current === sourceProjectId;
        if (stillSameProject) {
          setActionError(message);
          await refreshRunsIfCurrent(sourceProjectId, sourceWorkflowId, epoch);
        }
        return { ok: false, error: stillSameProject ? message : null };
      } finally {
        actionLock.current = false;
        if (mounted.current) setActionBusy(false);
      }
    },
    [refreshRunsIfCurrent],
  );

  const triggerRun = useCallback(
    (workflowId: string) => {
      if (!selectedProjectId) return Promise.resolve({ ok: false, error: null } as ActionResult);
      return withActionLock(() => runWorkflow(selectedProjectId, workflowId));
    },
    [selectedProjectId, withActionLock],
  );

  const replay = useCallback(
    (mode: ReplayMode) => {
      if (!selectedProjectId || !selectedRunId) {
        return Promise.resolve({ ok: false, error: null } as ActionResult);
      }
      return withActionLock(() => replayWorkflowRun(selectedProjectId, selectedRunId, mode));
    },
    [selectedProjectId, selectedRunId, withActionLock],
  );

  const resume = useCallback(() => {
    if (!selectedProjectId || !selectedRunId) {
      return Promise.resolve({ ok: false, error: null } as ActionResult);
    }
    return withActionLock(() => resumeWorkflowRun(selectedProjectId, selectedRunId));
  }, [selectedProjectId, selectedRunId, withActionLock]);

  useEffect(
    () => () => {
      mounted.current = false;
      projectAbort.current?.abort();
      workflowAbort.current?.abort();
      runAbort.current?.abort();
      lineageAbort.current?.abort();
      compareAbort.current?.abort();
      if (pollTimer.current) clearTimeout(pollTimer.current);
    },
    [],
  );

  return {
    projects,
    selectedProjectId,
    selectProject,
    createProject,
    datasets,
    workflows,
    revisions,
    runs,
    runsHasMore,
    loadMoreRuns,
    selectedWorkflow,
    selectedRunId,
    runDetail,
    view,
    loading,
    detailLoading,
    error,
    actionError,
    actionBusy,
    compare,
    compareError,
    compareBusy,
    comparePeerId,
    setComparePeerId: (id: string) => {
      setComparePeerId(id);
      setCompare(null);
      setCompareError(null);
    },
    lineageByArtifact,
    loadLineage,
    openWorkflow,
    openRun,
    back,
    openCompare,
    triggerRun,
    replay,
    resume,
  };
}
