/**
 * Fixture factories for the project workspace assets line (ADR-0143).
 *
 * The repo mocks the network at the API-module boundary (`vi.mock('@/lib/api/
 * project-assets', () => …)` — no msw dependency), so tests build response
 * payloads with these factories. Three canonical states per family:
 * `success` (populated), `empty`, and `error` (rejected promise / ApiError
 * built inline by the test).
 */

import type {
  ArtifactSummary,
  DataUsageResponse,
  DatasetAttached,
  GcExecuteResponse,
  GcPlanResponse,
  Page,
  QualityIssueSummary,
  RepairResponse,
  SnapshotRestoreResponse,
  SnapshotVerification,
  SpatialQualityReport,
  WorkspaceSnapshotListResponse,
  WorkspaceSnapshotSummary,
} from '@/lib/api/project-assets';
import type { LineageGraph } from '@/lib/api/project';
import type { ProjectDataset } from '@/lib/api/project';

export function emptyPage<T>(): Page<T> {
  return { items: [], total: 0, limit: 50, offset: 0, has_more: false };
}

export function makeDatasetRow(overrides: Partial<ProjectDataset> = {}): ProjectDataset {
  return {
    id: 'ds-1',
    project_id: 'p1',
    name: '人口格网 2024',
    source_type: 'layer',
    source_ref: 'cat-100',
    crs: 'EPSG:4326',
    quality_status: 'passed',
    created_at: '2026-09-01T08:00:00Z',
    ...overrides,
  };
}

export function makeAttachedDataset(overrides: Partial<DatasetAttached> = {}): DatasetAttached {
  return {
    ...makeDatasetRow(overrides),
    schema_profile: {
      geometry: 'Point',
      properties: [
        { name: 'grid_id', type: 'string' },
        { name: 'population', type: 'integer' },
      ],
    },
    ...overrides,
  } as DatasetAttached;
}

export function makeDatasetPage(n: number, overrides: Partial<ProjectDataset> = {}): Page<ProjectDataset> {
  return {
    items: Array.from({ length: n }, (_, i) =>
      makeDatasetRow({ id: `ds-${i + 1}`, name: `数据集 ${i + 1}`, ...overrides }),
    ),
    total: n,
    limit: 50,
    offset: 0,
    has_more: false,
  };
}

const ARTIFACT_TYPES = ['raster', 'vector', 'chart', 'report', 'map_spec'] as const;

export function makeArtifactRow(overrides: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return {
    id: 'art-1',
    project_id: 'p1',
    name: '坡度分析结果',
    artifact_type: 'raster',
    format: ' GeoTIFF'.trim(),
    crs: 'EPSG:4326',
    created_at: '2026-09-02T10:00:00Z',
    ...overrides,
  };
}

export function makeArtifactPage(n: number, overrides: Partial<ArtifactSummary> = {}): Page<ArtifactSummary> {
  return {
    items: Array.from({ length: n }, (_, i) =>
      makeArtifactRow({
        id: `art-${i + 1}`,
        name: `产物 ${i + 1}`,
        artifact_type: ARTIFACT_TYPES[i % ARTIFACT_TYPES.length],
        ...overrides,
      }),
    ),
    total: n,
    limit: 50,
    offset: 0,
    has_more: false,
  };
}

/**
 * Lineage graph over `nodeCount` artifacts (≥50 used by the render-perf
 * assertion). Chain topology with side consumers so parents AND consumers are
 * both populated; depths respect the backend BFS depth ≤5 contract.
 */
export function makeLineageGraph(nodeCount = 60, rootId = 'art-1'): LineageGraph {
  const half = Math.max(1, Math.floor(nodeCount / 2));
  const parents = Array.from({ length: half }, (_, i) => ({
    lineage_id: `lg-p${i + 1}`,
    artifact_id: rootId,
    parent_artifact_id: `art-up-${i + 1}`,
    producing_tool: i % 2 === 0 ? 'slope_analysis' : 'reproject_raster',
    tool_version: '1.4.2',
    workflow_run_id: `run-${(i % 7) + 1}`,
    parameters: { cell_size: 30 },
    source_dataset_id: i === 0 ? 'ds-1' : null,
    source_dataset_fingerprint: i === 0 ? 'fp-ds-1' : null,
    depth: Math.min(5, (i % 5) + 1),
    created_at: '2026-09-02T09:30:00Z',
  }));
  const consumers = Array.from({ length: nodeCount - half }, (_, i) => ({
    lineage_id: `lg-c${i + 1}`,
    consumer_artifact_id: `art-down-${i + 1}`,
    parent_artifact_id: rootId,
    producing_tool: 'zonal_stats',
    tool_version: '2.0.1',
    workflow_run_id: `run-${(i % 5) + 10}`,
    parameters: { statistic: 'mean' },
    depth: Math.min(5, (i % 5) + 1),
    created_at: '2026-09-02T11:00:00Z',
  }));
  return { artifact_id: rootId, parents, consumers };
}

export function makeSnapshotSummary(
  overrides: Partial<WorkspaceSnapshotSummary> = {},
): WorkspaceSnapshotSummary {
  return {
    snapshot_id: 'snap-1',
    label: '交付前基线',
    created_at: 1_757_600_000,
    artifacts: 6,
    layers: 4,
    project_id: 'p1',
    home: 'project',
    ...overrides,
  };
}

export function makeSnapshotList(n: number): WorkspaceSnapshotListResponse {
  return {
    project_id: 'p1',
    count: n,
    bounded: 50,
    items: Array.from({ length: n }, (_, i) =>
      makeSnapshotSummary({
        snapshot_id: `snap-${i + 1}`,
        label: i === 0 ? '交付前基线' : `自动保存 ${i}`,
        created_at: 1_757_600_000 - i * 3_600,
        artifacts: 6 - (i % 3),
        layers: 4 - (i % 2),
      }),
    ),
  };
}

export function makeVerification(overrides: Partial<SnapshotVerification> = {}): SnapshotVerification {
  return {
    snapshot_id: 'snap-1',
    exists: true,
    integrity_ok: true,
    restorable: true,
    artifacts: { total: 6, live: 6, missing: [] },
    layers: { total: 4, live: 4, missing: [] },
    mapspec_available: true,
    integrity: {
      'art-1': 'verified',
      'art-2': 'verified',
      'art-3': 'verified',
    },
    ...overrides,
  };
}

export function makeRestoreResponse(overrides: Partial<SnapshotRestoreResponse> = {}): SnapshotRestoreResponse {
  return {
    mode: 'register',
    verification: makeVerification(),
    ...overrides,
  };
}

export function makeIssueSummary(overrides: Partial<QualityIssueSummary> = {}): QualityIssueSummary {
  return { info: 4, warning: 2, error: 1, blocking: 0, ...overrides };
}

export function makeQualityReport(overrides: Partial<SpatialQualityReport> = {}): SpatialQualityReport {
  return {
    dataset_id: 'ds-1',
    total_features: 1_240,
    issue_summary: makeIssueSummary(),
    overall_status: 'warning',
    truncated: false,
    truncated_count: 0,
    issues: [
      {
        dimension: 'geometry',
        code: 'self_intersection',
        level: 'error',
        message: '要素 42 存在自相交环',
        feature_index: 42,
      },
      {
        dimension: 'schema',
        code: 'missing_attribute',
        level: 'warning',
        message: '17 个要素缺少 population 字段',
      },
    ],
    ...overrides,
  };
}

export function makeRepairResponse(overrides: Partial<RepairResponse> = {}): RepairResponse {
  return {
    project_id: 'p1',
    operations_applied: ['make_valid', 'remove_empty'],
    ops_evidence: { make_valid: { fixed: 3 }, remove_empty: { removed: 1 } },
    repair_logs: [{ op: 'make_valid', feature_index: 42, result: 'fixed' }],
    logs_count: 4,
    feature_count: 1_239,
    feature_count_before: 1_240,
    repaired_ref: 'ref:repaired-ds-1',
    ref_registration_error: null,
    repair_evidence: { duration_ms: 812 },
    lineage_status: 'recorded',
    lineage_artifact_id: 'art-repaired-1',
    lineage_error: null,
    repaired_geojson_preview: { type: 'FeatureCollection', features: [] },
    ...overrides,
  };
}

export function makeDataUsage(overrides: Partial<DataUsageResponse> = {}): DataUsageResponse {
  return {
    project_id: 'p1',
    usage: { bytes: 3.2e9, artifact_count: 48, revision_bytes: 1.1e9 },
    limits: { max_bytes: 10e9, max_artifact_count: 200, max_revision_bytes_per_artifact: 5e8 },
    quota: { allowed: true, reason: null },
    retention: {
      policy: { grace_hours: 72, max_revision_age_days: 30 },
      upcoming_candidates: 7,
      upcoming_candidate_blobs: 3,
    },
    ...overrides,
  };
}

export function makeGcPlan(overrides: Partial<GcPlanResponse> = {}): GcPlanResponse {
  return {
    project_id: 'p1',
    scoped_to_project: true,
    retention: {
      policy: { grace_hours: 72 },
      disabled: false,
      candidate_revision_count: 12,
      candidate_blob_count: 5,
      candidate_blob_bytes: 4.6e8,
      protected_counts: { pinned: 3 },
      protection_scan_truncated: false,
      candidate_revisions: [
        { artifact_id: 'art-9', revision_no: 3, age_days: 41, byte_size: 1.2e8 },
        { artifact_id: 'art-11', revision_no: 1, age_days: 55, byte_size: 8.4e7 },
      ],
      candidate_blobs: [{ sha_prefix: 'a1b2c3d4e5f6', byte_size: 2.1e8 }],
    },
    promotion_store_gc: {
      scoped_to_project: true,
      grace_hours: 72,
      deletable_count: 2,
      deletable_bytes: 3.3e8,
      deletable: [{ sha_prefix: 'f6e5d4c3b2a1', bytes: 3.3e8 }],
    },
    ...overrides,
  };
}

export function makeGcExecuteResponse(overrides: Partial<GcExecuteResponse> = {}): GcExecuteResponse {
  return {
    project_id: 'p1',
    retention: {
      deleted_revisions: [{ artifact_id: 'art-9', revision_no: 3 }],
      deleted_revision_count: 12,
      deleted_blobs: [{ sha256: 'a1b2c3' }],
      deleted_blob_count: 5,
      bytes_freed: 7.9e8,
      skipped_protected_count: 1,
      skipped_protected: [{ artifact_id: 'art-2' }],
    },
    orphan_revisions: { deleted_count: 0, deleted_revision_ids: [] },
    skipped_protected: [{ key: 'art-2', reason: 'pinned' }],
    ...overrides,
  };
}
