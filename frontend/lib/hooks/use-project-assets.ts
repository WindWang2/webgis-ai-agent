/**
 * Project workspace assets hooks (ADR-0143) — datasets / artifacts /
 * snapshots / quality / data-gc.
 *
 * Discipline mirrors `use-workflow-workspace.ts`:
 *   - AbortController per project switch + monotonic generation so a stale
 *     response can never overwrite the current view;
 *   - per-action busy locks so destructive actions cannot double-submit;
 *   - bounded, on-demand fetching (no polling — project.py exposes no job
 *     handles, every mutation is a synchronous call);
 *   - unmount aborts everything.
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  attachDataset,
  auditSpatialQuality,
  cloneArtifact,
  deleteWorkspaceSnapshot,
  detachDataset,
  executeDataGc,
  fetchDataUsage,
  fetchProjectArtifacts,
  fetchProjectDatasetPage,
  inspectWorkspaceSnapshot,
  listWorkspaceSnapshots,
  pinArtifact,
  planDataGc,
  repairQuality,
  restoreWorkspaceSnapshot,
  saveWorkspaceSnapshot,
  unpinArtifact,
  type ArtifactCloneResponse,
  type ArtifactPinResponse,
  type ArtifactSummary,
  type DataUsageResponse,
  type DatasetAttachRequest,
  type DatasetAttached,
  type GcExecuteResponse,
  type GcPlanResponse,
  type Page,
  type ProjectDataset,
  type RepairRequest,
  type RepairResponse,
  type SnapshotRestoreResponse,
  type SnapshotSaveRequest,
  type SnapshotVerification,
  type SpatialQualityReport,
  type WorkspaceSnapshotListResponse,
  type WorkspaceSnapshotSaveResponse,
  type WorkspaceSnapshotSummary,
} from '@/lib/api/project-assets';
import { fetchArtifactLineage, type LineageGraph } from '@/lib/api/project';
import { isAbortError, parseApiErrorDetail } from '@/lib/workflow/recovery';

interface FetchOpts {
  signal?: AbortSignal;
  forceRefresh?: boolean;
}

/** Client-side dependency hint shown before a detach — the backend does none. */
export function datasetDependencyWarning(dataset: Pick<ProjectDataset, 'source_type'>): string {
  if (dataset.source_type === 'layer') {
    return '该数据集来自图层挂载：解绑不影响图层本体，但引用它的工作流与血缘记录将失去输入来源。';
  }
  return '后端为软删除（保留行以维持血缘可解析），不做引用检查——正在使用它的工作流可能失败。';
}

// ── datasets ────────────────────────────────────────────────────────────────

export interface UseProjectDatasetsResult {
  datasets: ProjectDataset[];
  total: number;
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  /** Per-row action lock id ('attach' while attaching), '' when idle. */
  busyId: string;
  /** schema_profile captured from attach responses (list endpoint omits it). */
  schemaByDataset: Record<string, Record<string, unknown>>;
  reload: (opts?: FetchOpts) => Promise<void>;
  loadMore: () => Promise<void>;
  attach: (req: DatasetAttachRequest) => Promise<DatasetAttached | null>;
  detach: (datasetId: string) => Promise<boolean>;
}

export function useProjectDatasets(projectId: string): UseProjectDatasetsResult {
  const [datasets, setDatasets] = useState<ProjectDataset[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState('');
  const [schemaByDataset, setSchemaByDataset] = useState<Record<string, Record<string, unknown>>>({});

  const gen = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const pageSize = 50;

  const applyPage = useCallback((page: Page<ProjectDataset>, append: boolean) => {
    setDatasets((prev) => {
      if (!append) return page.items;
      const seen = new Set(prev.map((d) => d.id));
      return [...prev, ...page.items.filter((d) => !seen.has(d.id))];
    });
    setTotal(page.total);
    setHasMore(page.has_more);
  }, []);

  const reload = useCallback(
    async (opts?: FetchOpts) => {
      if (!projectId) {
        setDatasets([]);
        setTotal(0);
        setHasMore(false);
        return;
      }
      abort.current?.abort();
      const ac = new AbortController();
      abort.current = ac;
      const g = ++gen.current;
      setLoading(true);
      setError(null);
      try {
        const page = await fetchProjectDatasetPage(projectId, {
          limit: pageSize,
          offset: 0,
          signal: ac.signal,
          forceRefresh: opts?.forceRefresh,
        });
        if (g !== gen.current || !mounted.current) return;
        applyPage(page, false);
      } catch (err: unknown) {
        if (ac.signal.aborted || isAbortError(err) || g !== gen.current) return;
        setError(parseApiErrorDetail(err, '加载数据集失败'));
      } finally {
        if (g === gen.current && mounted.current) setLoading(false);
      }
    },
    [projectId, applyPage],
  );

  const loadMore = useCallback(async () => {
    if (!projectId || !hasMore) return;
    const g = gen.current;
    try {
      const page = await fetchProjectDatasetPage(projectId, {
        limit: pageSize,
        offset: datasets.length,
        forceRefresh: true,
      });
      if (g !== gen.current || !mounted.current) return;
      applyPage(page, true);
    } catch (err: unknown) {
      if (isAbortError(err) || g !== gen.current) return;
      setError(parseApiErrorDetail(err, '加载更多数据集失败'));
    }
  }, [projectId, hasMore, datasets.length, applyPage]);

  const attach = useCallback(
    async (req: DatasetAttachRequest) => {
      if (!projectId || busyId) return null;
      setBusyId('attach');
      try {
        const attached = await attachDataset(projectId, req);
        if (!mounted.current) return attached;
        setSchemaByDataset((prev) => ({
          ...prev,
          [attached.id]: attached.schema_profile ?? {},
        }));
        setDatasets((prev) => (prev.some((d) => d.id === attached.id) ? prev : [attached, ...prev]));
        setTotal((t) => t + 1);
        return attached;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '挂载数据集失败'));
        return null;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [projectId, busyId],
  );

  const detach = useCallback(
    async (datasetId: string) => {
      if (!projectId || busyId) return false;
      setBusyId(datasetId);
      try {
        await detachDataset(projectId, datasetId);
        if (!mounted.current) return true;
        setDatasets((prev) => prev.filter((d) => d.id !== datasetId));
        setTotal((t) => Math.max(0, t - 1));
        return true;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '解绑数据集失败'));
        return false;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [projectId, busyId],
  );

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abort.current?.abort();
    };
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    datasets,
    total,
    hasMore,
    loading,
    error,
    busyId,
    schemaByDataset,
    reload,
    loadMore,
    attach,
    detach,
  };
}

// ── artifacts ───────────────────────────────────────────────────────────────

export type LineageState = 'loading' | 'empty' | 'error' | LineageGraph;

const PAGE_SIZE = 50;

export interface UseProjectArtifactsResult {
  artifacts: ArtifactSummary[];
  total: number;
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  busyId: string;
  /** Volatile pin state — the list endpoint does not expose `pinned`. */
  pinnedLocal: Record<string, boolean>;
  /** Pin receipts (revision_no / sha256) surfaced in the UI. */
  pinReceipts: Record<string, ArtifactPinResponse>;
  cloneResult: ArtifactCloneResponse | null;
  lineage: Record<string, LineageState>;
  typeFilter: string;
  setTypeFilter: (t: string) => void;
  reload: (opts?: FetchOpts) => Promise<void>;
  loadMore: () => Promise<void>;
  pin: (artifactId: string) => Promise<boolean>;
  unpin: (artifactId: string) => Promise<boolean>;
  clone: (artifactId: string) => Promise<ArtifactCloneResponse | null>;
  loadLineage: (artifactId: string) => Promise<void>;
}

export function useProjectArtifacts(projectId: string): UseProjectArtifactsResult {
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState('');
  const [pinnedLocal, setPinnedLocal] = useState<Record<string, boolean>>({});
  const [pinReceipts, setPinReceipts] = useState<Record<string, ArtifactPinResponse>>({});
  const [cloneResult, setCloneResult] = useState<ArtifactCloneResponse | null>(null);
  const [lineage, setLineage] = useState<Record<string, LineageState>>({});
  const [typeFilter, setTypeFilter] = useState('');

  const gen = useRef(0);
  const lineageAbort = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  const reload = useCallback(
    async (opts?: FetchOpts) => {
      if (!projectId) {
        setArtifacts([]);
        setTotal(0);
        setHasMore(false);
        return;
      }
      const ac = new AbortController();
      const g = ++gen.current;
      setLoading(true);
      setError(null);
      try {
        const page = await fetchProjectArtifacts(projectId, {
          limit: PAGE_SIZE,
          offset: 0,
          signal: ac.signal,
          forceRefresh: opts?.forceRefresh,
        });
        if (g !== gen.current || !mounted.current) return;
        setArtifacts(page.items);
        setTotal(page.total);
        setHasMore(page.has_more);
      } catch (err: unknown) {
        if (ac.signal.aborted || isAbortError(err) || g !== gen.current) return;
        setError(parseApiErrorDetail(err, '加载产物失败'));
      } finally {
        if (g === gen.current && mounted.current) setLoading(false);
      }
    },
    [projectId],
  );

  const loadMore = useCallback(async () => {
    if (!projectId || !hasMore) return;
    const g = gen.current;
    try {
      const page = await fetchProjectArtifacts(projectId, {
        limit: PAGE_SIZE,
        offset: artifacts.length,
        forceRefresh: true,
      });
      if (g !== gen.current || !mounted.current) return;
      setArtifacts((prev) => {
        const seen = new Set(prev.map((a) => a.id));
        return [...prev, ...page.items.filter((a) => !seen.has(a.id))];
      });
      setTotal(page.total);
      setHasMore(page.has_more);
    } catch (err: unknown) {
      if (isAbortError(err) || g !== gen.current) return;
      setError(parseApiErrorDetail(err, '加载更多产物失败'));
    }
  }, [projectId, hasMore, artifacts.length]);

  const pin = useCallback(
    async (artifactId: string) => {
      if (busyId) return false;
      setBusyId(artifactId);
      try {
        const receipt = await pinArtifact(artifactId, true);
        if (!mounted.current) return true;
        setPinReceipts((prev) => ({ ...prev, [artifactId]: receipt }));
        setPinnedLocal((prev) => ({ ...prev, [artifactId]: true }));
        return true;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '固定产物失败'));
        return false;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [busyId],
  );

  const unpin = useCallback(
    async (artifactId: string) => {
      if (busyId) return false;
      setBusyId(artifactId);
      try {
        const receipt = await unpinArtifact(artifactId);
        if (!mounted.current) return true;
        setPinReceipts((prev) => ({ ...prev, [artifactId]: receipt }));
        setPinnedLocal((prev) => ({ ...prev, [artifactId]: false }));
        return true;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '取消固定失败'));
        return false;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [busyId],
  );

  const clone = useCallback(
    async (artifactId: string) => {
      if (busyId) return null;
      setBusyId(artifactId);
      try {
        const result = await cloneArtifact(artifactId);
        if (mounted.current) setCloneResult(result);
        return result;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '克隆产物失败'));
        return null;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [busyId],
  );

  const loadLineage = useCallback(
    async (artifactId: string) => {
      if (!artifactId) return;
      const existing = lineage[artifactId];
      if (existing && existing !== 'error') return;
      lineageAbort.current?.abort();
      const ac = new AbortController();
      lineageAbort.current = ac;
      const g = gen.current;
      setLineage((prev) => ({ ...prev, [artifactId]: 'loading' }));
      try {
        const graph = await fetchArtifactLineage(artifactId, { signal: ac.signal });
        if (!mounted.current || g !== gen.current) return;
        const empty = (graph.parents?.length ?? 0) === 0 && (graph.consumers?.length ?? 0) === 0;
        setLineage((prev) => ({ ...prev, [artifactId]: empty ? 'empty' : graph }));
      } catch (err: unknown) {
        if (ac.signal.aborted || isAbortError(err) || g !== gen.current) return;
        setLineage((prev) => ({ ...prev, [artifactId]: 'error' }));
      }
    },
    [lineage],
  );

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lineageAbort.current?.abort();
    };
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const visible = typeFilter
    ? artifacts.filter((a) => a.artifact_type === typeFilter)
    : artifacts;

  return {
    artifacts: visible,
    total,
    hasMore,
    loading,
    error,
    busyId,
    pinnedLocal,
    pinReceipts,
    cloneResult,
    lineage,
    typeFilter,
    setTypeFilter,
    reload,
    loadMore,
    pin,
    unpin,
    clone,
    loadLineage,
  };
}

// ── workspace snapshots ─────────────────────────────────────────────────────

export interface SnapshotDiffResult {
  a: SnapshotVerification;
  b: SnapshotVerification;
  artifacts: {
    totalDelta: number;
    missingAdded: string[];
    missingRemoved: string[];
  };
  layers: {
    totalDelta: number;
    missingAdded: string[];
    missingRemoved: string[];
  };
  integrityChanges: Array<{ id: string; from: string; to: string }>;
  mapspecChanged: boolean;
}

/**
 * Client-side aggregation: two verify reports → structured diff. The backend
 * has no snapshot-diff endpoint (recon doc §2.10) — this compares liveness,
 * integrity map, and mapspec availability between the two reports.
 */
export function diffSnapshots(a: SnapshotVerification, b: SnapshotVerification): SnapshotDiffResult {
  const addedIn = (from: string[], to: string[]) => {
    const t = new Set(to);
    return from.filter((x) => !t.has(x));
  };
  const integrityChanges: SnapshotDiffResult['integrityChanges'] = [];
  const ids = new Set([...Object.keys(a.integrity), ...Object.keys(b.integrity)]);
  for (const id of ids) {
    const from = a.integrity[id] ?? 'pointer_missing';
    const to = b.integrity[id] ?? 'pointer_missing';
    if (from !== to) integrityChanges.push({ id, from, to });
  }
  return {
    a,
    b,
    artifacts: {
      totalDelta: b.artifacts.total - a.artifacts.total,
      missingAdded: addedIn(b.artifacts.missing, a.artifacts.missing),
      missingRemoved: addedIn(a.artifacts.missing, b.artifacts.missing),
    },
    layers: {
      totalDelta: b.layers.total - a.layers.total,
      missingAdded: addedIn(b.layers.missing, a.layers.missing),
      missingRemoved: addedIn(a.layers.missing, b.layers.missing),
    },
    integrityChanges,
    mapspecChanged: a.mapspec_available !== b.mapspec_available,
  };
}

export interface UseProjectSnapshotsResult {
  snapshots: WorkspaceSnapshotSummary[];
  count: number;
  bounded: number;
  loading: boolean;
  error: string | null;
  busyId: string;
  /** Verify reports cached per snapshot id. */
  verifications: Record<string, SnapshotVerification>;
  diff: SnapshotDiffResult | null;
  diffLoading: boolean;
  /** Snapshot actions need a session context — absent disables them. */
  sessionReady: boolean;
  reload: (opts?: FetchOpts) => Promise<void>;
  save: (req: Omit<SnapshotSaveRequest, 'session_id'>) => Promise<WorkspaceSnapshotSaveResponse | null>;
  inspect: (snapshotId: string) => Promise<SnapshotVerification | null>;
  restore: (snapshotId: string, mode: 'verify' | 'register') => Promise<SnapshotRestoreResponse | null>;
  remove: (snapshotId: string) => Promise<boolean>;
  loadDiff: (aId: string, bId: string) => Promise<SnapshotDiffResult | null>;
  clearDiff: () => void;
}

export function useProjectSnapshots(
  projectId: string,
  sessionId?: string | null,
): UseProjectSnapshotsResult {
  const [list, setList] = useState<WorkspaceSnapshotListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState('');
  const [verifications, setVerifications] = useState<Record<string, SnapshotVerification>>({});
  const [diff, setDiff] = useState<SnapshotDiffResult | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const gen = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const sessionReady = Boolean(sessionId);

  const reload = useCallback(
    async (opts?: FetchOpts) => {
      if (!projectId) {
        setList(null);
        return;
      }
      abort.current?.abort();
      const ac = new AbortController();
      abort.current = ac;
      const g = ++gen.current;
      setLoading(true);
      setError(null);
      try {
        const result = await listWorkspaceSnapshots(projectId, {
          signal: ac.signal,
          forceRefresh: opts?.forceRefresh,
          sessionId: sessionId ?? undefined,
        });
        if (g !== gen.current || !mounted.current) return;
        setList(result);
      } catch (err: unknown) {
        if (ac.signal.aborted || isAbortError(err) || g !== gen.current) return;
        setError(parseApiErrorDetail(err, '加载快照失败'));
      } finally {
        if (g === gen.current && mounted.current) setLoading(false);
      }
    },
    [projectId, sessionId],
  );

  const save = useCallback(
    async (req: Omit<SnapshotSaveRequest, 'session_id'>) => {
      if (!projectId || !sessionId || busyId) return null;
      setBusyId('save');
      try {
        const saved = await saveWorkspaceSnapshot(projectId, { ...req, session_id: sessionId });
        if (mounted.current) void reload({ forceRefresh: true });
        return saved;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '保存快照失败'));
        return null;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [projectId, sessionId, busyId, reload],
  );

  const inspect = useCallback(
    async (snapshotId: string) => {
      if (!projectId || !sessionId) return null;
      try {
        const report = await inspectWorkspaceSnapshot(projectId, snapshotId, sessionId, {
          forceRefresh: true,
        });
        if (mounted.current) setVerifications((prev) => ({ ...prev, [snapshotId]: report }));
        return report;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '核查快照失败'));
        return null;
      }
    },
    [projectId, sessionId],
  );

  const restore = useCallback(
    async (snapshotId: string, mode: 'verify' | 'register') => {
      if (!projectId || !sessionId || busyId) return null;
      setBusyId(snapshotId);
      try {
        const result = await restoreWorkspaceSnapshot(projectId, snapshotId, {
          session_id: sessionId,
          mode,
        });
        if (mounted.current) {
          setVerifications((prev) => ({ ...prev, [snapshotId]: result.verification }));
        }
        return result;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '恢复快照失败'));
        return null;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [projectId, sessionId, busyId],
  );

  const remove = useCallback(
    async (snapshotId: string) => {
      if (!projectId || !sessionId || busyId) return false;
      setBusyId(snapshotId);
      try {
        await deleteWorkspaceSnapshot(projectId, snapshotId, sessionId);
        if (!mounted.current) return true;
        setList((prev) =>
          prev
            ? {
                ...prev,
                count: Math.max(0, prev.count - 1),
                items: prev.items.filter((s) => s.snapshot_id !== snapshotId),
              }
            : prev,
        );
        return true;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '删除快照失败'));
        return false;
      } finally {
        if (mounted.current) setBusyId('');
      }
    },
    [projectId, sessionId, busyId],
  );

  const loadDiff = useCallback(
    async (aId: string, bId: string) => {
      if (!projectId || !sessionId || aId === bId) return null;
      setDiffLoading(true);
      setError(null);
      try {
        const [a, b] = await Promise.all([
          inspectWorkspaceSnapshot(projectId, aId, sessionId, { forceRefresh: true }),
          inspectWorkspaceSnapshot(projectId, bId, sessionId, { forceRefresh: true }),
        ]);
        if (!mounted.current) return null;
        setVerifications((prev) => ({ ...prev, [aId]: a, [bId]: b }));
        const result = diffSnapshots(a, b);
        setDiff(result);
        return result;
      } catch (err: unknown) {
        if (mounted.current) setError(parseApiErrorDetail(err, '快照对比失败'));
        return null;
      } finally {
        if (mounted.current) setDiffLoading(false);
      }
    },
    [projectId, sessionId],
  );

  const clearDiff = useCallback(() => setDiff(null), []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abort.current?.abort();
    };
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    snapshots: list?.items ?? [],
    count: list?.count ?? 0,
    bounded: list?.bounded ?? 50,
    loading,
    error,
    busyId,
    verifications,
    diff,
    diffLoading,
    sessionReady,
    reload,
    save,
    inspect,
    restore,
    remove,
    loadDiff,
    clearDiff,
  };
}

// ── quality ─────────────────────────────────────────────────────────────────

export interface UseProjectQualityResult {
  phase: 'idle' | 'auditing' | 'reported' | 'repairing' | 'repaired';
  report: SpatialQualityReport | null;
  repair: RepairResponse | null;
  error: string | null;
  busy: boolean;
  /** GeoJSON held between audit and repair so repair reuses the same input. */
  sourceGeojson: Record<string, unknown> | null;
  audit: (geojson: Record<string, unknown>, opts?: { crs?: string; datasetId?: string }) => Promise<SpatialQualityReport | null>;
  runRepair: (req: Omit<RepairRequest, 'geojson'>) => Promise<RepairResponse | null>;
  reset: () => void;
}

export function useProjectQuality(projectId: string): UseProjectQualityResult {
  const [phase, setPhase] = useState<UseProjectQualityResult['phase']>('idle');
  const [report, setReport] = useState<SpatialQualityReport | null>(null);
  const [repairResult, setRepairResult] = useState<RepairResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceGeojson, setSourceGeojson] = useState<Record<string, unknown> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const reportRef = useRef<SpatialQualityReport | null>(null);
  reportRef.current = report;

  const audit = useCallback(
    async (geojson: Record<string, unknown>, opts?: { crs?: string; datasetId?: string }) => {
      if (!projectId) return null;
      abort.current?.abort();
      const ac = new AbortController();
      abort.current = ac;
      setError(null);
      setPhase('auditing');
      try {
        const result = await auditSpatialQuality(projectId, geojson, { crs: opts?.crs, signal: ac.signal });
        if (!mounted.current) return null;
        const scoped = opts?.datasetId ? { ...result, dataset_id: opts.datasetId } : result;
        setReport(scoped);
        setSourceGeojson(geojson);
        setPhase('reported');
        setRepairResult(null);
        return scoped;
      } catch (err: unknown) {
        if (ac.signal.aborted || isAbortError(err)) return null;
        if (mounted.current) {
          setError(parseApiErrorDetail(err, '质量审计失败'));
          setPhase(reportRef.current ? 'reported' : 'idle');
        }
        return null;
      }
    },
    [projectId],
  );

  const runRepair = useCallback(
    async (req: Omit<RepairRequest, 'geojson'>) => {
      if (!projectId || !sourceGeojson) return null;
      setError(null);
      setPhase('repairing');
      try {
        const result = await repairQuality(projectId, { ...req, geojson: sourceGeojson });
        if (!mounted.current) return null;
        setRepairResult(result);
        setPhase('repaired');
        return result;
      } catch (err: unknown) {
        if (mounted.current) {
          setError(parseApiErrorDetail(err, '质量修复失败'));
          setPhase(reportRef.current ? 'reported' : 'idle');
        }
        return null;
      }
    },
    [projectId, sourceGeojson],
  );

  const reset = useCallback(() => {
    setPhase('idle');
    setReport(null);
    setRepairResult(null);
    setError(null);
    setSourceGeojson(null);
  }, []);

  useEffect(
    () => () => {
      mounted.current = false;
      abort.current?.abort();
    },
    [],
  );

  return {
    phase,
    report,
    repair: repairResult,
    error,
    busy: phase === 'auditing' || phase === 'repairing',
    sourceGeojson,
    audit,
    runRepair,
    reset,
  };
}

// ── data-gc ─────────────────────────────────────────────────────────────────

export interface UseDataGcResult {
  usage: DataUsageResponse | null;
  plan: GcPlanResponse | null;
  executed: GcExecuteResponse | null;
  usageLoading: boolean;
  planLoading: boolean;
  executing: boolean;
  error: string | null;
  loadUsage: (opts?: FetchOpts) => Promise<void>;
  runPlan: () => Promise<GcPlanResponse | null>;
  execute: () => Promise<GcExecuteResponse | null>;
  resetExecution: () => void;
}

export function useDataGc(projectId: string): UseDataGcResult {
  const [usage, setUsage] = useState<DataUsageResponse | null>(null);
  const [plan, setPlan] = useState<GcPlanResponse | null>(null);
  const [executed, setExecuted] = useState<GcExecuteResponse | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const gen = useRef(0);
  const mounted = useRef(true);

  const loadUsage = useCallback(
    async (opts?: FetchOpts) => {
      if (!projectId) return;
      const g = ++gen.current;
      setUsageLoading(true);
      setError(null);
      try {
        const result = await fetchDataUsage(projectId, { forceRefresh: opts?.forceRefresh });
        if (g !== gen.current || !mounted.current) return;
        setUsage(result);
      } catch (err: unknown) {
        if (isAbortError(err) || g !== gen.current) return;
        if (mounted.current) setError(parseApiErrorDetail(err, '加载用量失败'));
      } finally {
        if (g === gen.current && mounted.current) setUsageLoading(false);
      }
    },
    [projectId],
  );

  const runPlan = useCallback(async () => {
    if (!projectId) return null;
    setPlanLoading(true);
    setError(null);
    try {
      const result = await planDataGc(projectId);
      if (!mounted.current) return null;
      setPlan(result);
      return result;
    } catch (err: unknown) {
      if (mounted.current) setError(parseApiErrorDetail(err, '生成回收计划失败'));
      return null;
    } finally {
      if (mounted.current) setPlanLoading(false);
    }
  }, [projectId]);

  const execute = useCallback(async () => {
    if (!projectId || executing) return null;
    setExecuting(true);
    setError(null);
    try {
      const result = await executeDataGc(projectId);
      if (!mounted.current) return null;
      setExecuted(result);
      void loadUsage({ forceRefresh: true });
      return result;
    } catch (err: unknown) {
      if (mounted.current) setError(parseApiErrorDetail(err, '执行数据回收失败'));
      return null;
    } finally {
      if (mounted.current) setExecuting(false);
    }
  }, [projectId, executing, loadUsage]);

  const resetExecution = useCallback(() => setExecuted(null), []);

  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );

  return {
    usage,
    plan,
    executed,
    usageLoading,
    planLoading,
    executing,
    error,
    loadUsage,
    runPlan,
    execute,
    resetExecution,
  };
}
