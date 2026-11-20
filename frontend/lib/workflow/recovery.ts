/**
 * Workflow recoverability helpers.
 *
 * Pure functions over backend contracts. They never invent resume availability,
 * fingerprint equality, or a CRS default — those come from the API.
 */

import { ApiError } from '@/lib/api/transport';
import { t } from '@/lib/i18n/t';
import type {
  LineageGraph,
  Page,
  RunComparison,
  WorkflowRunDetail,
  WorkflowRunStatus,
} from '@/lib/api/project';

const RUN_STATUS_KEY: Record<WorkflowRunStatus, string> = {
  pending: 'workbench.recovery.statusPending',
  running: 'workbench.recovery.statusRunning',
  completed: 'workbench.recovery.statusCompleted',
  failed: 'workbench.recovery.statusFailed',
  cancelled: 'workbench.recovery.statusCancelled',
};

export function formatCrs(crs: string | null | undefined): string {
  if (typeof crs !== 'string') return t('workbench.recovery.crsUnknown');
  const trimmed = crs.trim();
  return trimmed ? trimmed : t('workbench.recovery.crsUnknown');
}

export function isPartialRun(
  run: Pick<WorkflowRunDetail, 'status' | 'completed_steps'> | null | undefined,
): boolean {
  if (!run) return false;
  if (run.status !== 'failed' && run.status !== 'cancelled') return false;
  return (run.completed_steps?.length ?? 0) > 0;
}

/**
 * Necessary (not sufficient) preconditions to *offer* resume.
 * The backend still decides; a 409 is the authority.
 */
export function shouldOfferResume(
  run: Pick<WorkflowRunDetail, 'status' | 'completed_steps'> | null | undefined,
): boolean {
  if (!run) return false;
  if (run.status !== 'failed' && run.status !== 'cancelled') return false;
  return (run.completed_steps?.length ?? 0) > 0;
}

export function runStatusLabel(status: string): string {
  const key = RUN_STATUS_KEY[status as WorkflowRunStatus];
  return key ? t(key) : status;
}

export type OutcomeToastVariant = 'success' | 'info' | 'warning' | 'error';

/** Toast chrome follows run status, not HTTP 2xx. */
export function outcomeToastVariant(status: string): OutcomeToastVariant {
  if (status === 'completed') return 'success';
  if (status === 'cancelled') return 'warning';
  if (status === 'pending' || status === 'running') return 'info';
  return 'error';
}

export function formatOutcomeMessage(
  action: 'replay' | 'resume' | 'run',
  run: Pick<WorkflowRunDetail, 'status'>,
): string {
  const verb = t(
    action === 'replay'
      ? 'workbench.recovery.verbReplay'
      : action === 'resume'
        ? 'workbench.recovery.verbResume'
        : 'workbench.recovery.verbRun',
  );
  return t('workbench.recovery.outcome', { verb, status: runStatusLabel(run.status) });
}

export function runFingerprintsEqual(cmp: Pick<RunComparison, 'run_fingerprint'>): boolean {
  return cmp.run_fingerprint?.same === true;
}

export interface CompareRow {
  key: string;
  label: string;
  changed: boolean;
  detail: string;
}

function dictDiffKeys(block: { diff_keys?: string[] } | undefined): string[] {
  return Array.isArray(block?.diff_keys) ? block!.diff_keys : [];
}

export function summarizeCompare(cmp: RunComparison): CompareRow[] {
  const rows: CompareRow[] = [];
  const bothGraphFp =
    Boolean(cmp.revision?.run_a_graph_fingerprint) && Boolean(cmp.revision?.run_b_graph_fingerprint);
  const revChanged =
    (bothGraphFp && cmp.revision?.graph_same === false) ||
    cmp.revision?.run_a_revision !== cmp.revision?.run_b_revision;
  if (revChanged) {
    rows.push({
      key: 'revision',
      label: t('workbench.recovery.cmpRevision'),
      changed: true,
      detail: t('workbench.recovery.cmpRevisionDetail', {
        a: cmp.revision?.run_a_revision ?? '—',
        b: cmp.revision?.run_b_revision ?? '—',
      }),
    });
  }
  const inputKeys = dictDiffKeys(cmp.inputs_changed);
  if (inputKeys.length) {
    rows.push({
      key: 'inputs',
      label: t('workbench.recovery.cmpInputs'),
      changed: true,
      detail: inputKeys.join(', '),
    });
  }
  const dsKeys = dictDiffKeys(cmp.dataset_versions_changed);
  if (dsKeys.length) {
    rows.push({
      key: 'datasets',
      label: t('workbench.recovery.cmpDatasets'),
      changed: true,
      detail: dsKeys.join(', '),
    });
  }
  const toolKeys = Object.keys(cmp.tool_versions_changed ?? {});
  if (toolKeys.length) {
    rows.push({
      key: 'tools',
      label: t('workbench.recovery.cmpTools'),
      changed: true,
      detail: toolKeys.join(', '),
    });
  }
  const paramKeys = Object.keys(cmp.params_changed ?? {});
  if (paramKeys.length) {
    rows.push({
      key: 'params',
      label: t('workbench.recovery.cmpParams'),
      changed: true,
      detail: paramKeys.join(', '),
    });
  }
  const arts = cmp.output_artifacts_changed ?? {};
  const artChanged =
    arts.run_a_artifact_count !== arts.run_b_artifact_count ||
    JSON.stringify(arts.run_a_fingerprints ?? []) !== JSON.stringify(arts.run_b_fingerprints ?? []);
  if (artChanged) {
    rows.push({
      key: 'artifacts',
      label: t('workbench.recovery.cmpArtifacts'),
      changed: true,
      detail: `${arts.run_a_artifact_count ?? 0} → ${arts.run_b_artifact_count ?? 0}`,
    });
  }
  if (cmp.run_fingerprint && cmp.run_fingerprint.same !== true) {
    rows.push({
      key: 'fingerprint',
      label: t('workbench.recovery.cmpFingerprint'),
      changed: true,
      detail: t('workbench.recovery.cmpFingerprintDetail'),
    });
  }
  const warnings = cmp.warnings_changed ?? {};
  if (warnings.run_a_error || warnings.run_b_error) {
    rows.push({
      key: 'warnings',
      label: t('workbench.recovery.cmpWarnings'),
      changed: true,
      detail: `${String(warnings.run_a_error ?? '—')} → ${String(warnings.run_b_error ?? '—')}`,
    });
  }
  const metrics = cmp.metrics_changed ?? {};
  if (metrics.run_a_perf || metrics.run_b_perf) {
    rows.push({
      key: 'metrics',
      label: t('workbench.recovery.cmpMetrics'),
      changed: true,
      detail: t('workbench.recovery.cmpMetricsDetail'),
    });
  }
  return rows;
}

export function isAbortError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const name = (err as { name?: string }).name;
  return name === 'AbortError';
}

export function parseApiErrorDetail(err: unknown, fallback: string): string {
  if (isAbortError(err)) return fallback;
  if (err instanceof ApiError) {
    const body = err.body;
    if (typeof body === 'string' && body.trim()) return body;
    if (body && typeof body === 'object') {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === 'string' && detail.trim()) return detail;
      if (Array.isArray(detail)) {
        const parts = detail
          .map((item) => {
            if (typeof item === 'string') return item;
            if (item && typeof item === 'object' && 'msg' in item) {
              return String((item as { msg: unknown }).msg);
            }
            return '';
          })
          .filter(Boolean);
        if (parts.length) return parts.join('; ');
      }
    }
    return err.message || fallback;
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export function unwrapPage<T>(data: Page<T> | T[] | null | undefined): T[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}

export interface LineageRow {
  key: string;
  direction: 'upstream' | 'downstream';
  depth: number;
  nodeId: string;
  relatedId: string | null;
  tool: string;
  toolVersion: string | null;
  sourceDatasetId: string | null;
  sourceDatasetFingerprint: string | null;
}

const LINEAGE_CAP = 48;

export function lineageTruncated(graph: LineageGraph, maxRows = LINEAGE_CAP): boolean {
  const total = (graph.parents?.length ?? 0) + (graph.consumers?.length ?? 0);
  return total > maxRows;
}

export function buildLineageRows(graph: LineageGraph, maxRows = LINEAGE_CAP): LineageRow[] {
  const rows: LineageRow[] = [];
  for (const p of graph.parents ?? []) {
    rows.push({
      key: p.lineage_id || `up-${p.parent_artifact_id}-${p.depth}`,
      direction: 'upstream',
      depth: p.depth ?? 0,
      nodeId: p.parent_artifact_id,
      relatedId: p.artifact_id,
      tool: p.producing_tool || '—',
      toolVersion: p.tool_version ?? null,
      sourceDatasetId: p.source_dataset_id ?? null,
      sourceDatasetFingerprint: p.source_dataset_fingerprint ?? null,
    });
  }
  for (const c of graph.consumers ?? []) {
    rows.push({
      key: c.lineage_id || `down-${c.consumer_artifact_id}-${c.depth}`,
      direction: 'downstream',
      depth: c.depth ?? 0,
      nodeId: c.consumer_artifact_id,
      relatedId: c.parent_artifact_id,
      tool: c.producing_tool || '—',
      toolVersion: c.tool_version ?? null,
      sourceDatasetId: null,
      sourceDatasetFingerprint: null,
    });
  }
  return rows.slice(0, Math.max(0, maxRows));
}

export function isActiveRunStatus(status: string | undefined): boolean {
  return status === 'pending' || status === 'running';
}

export function shortId(id: string | null | undefined, len = 8): string {
  if (!id) return '—';
  return id.length <= len ? id : id.slice(0, len);
}

export function replayModeCopy(): {
  exact: { label: string; description: string };
  latest: { label: string; description: string };
} {
  return {
    exact: {
      label: t('workbench.recovery.replayExact'),
      description: t('workbench.recovery.replayExactDetail'),
    },
    latest: {
      label: t('workbench.recovery.replayLatest'),
      description: t('workbench.recovery.replayLatestDetail'),
    },
  };
}
