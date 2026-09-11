/**
 * workflow-runtime 端点 fixtures（三态，ADR-0142 D4）。
 * 形状逐字对照 /api/v1/workflow-runtime 实际投影（ops-console-recon.md §3），
 * 节点态覆盖 NodeState 九值词表。
 */
import type {
  DebugBundle,
  RecomputePlanView,
  RuntimeJournalEvent,
  WorkflowInstance,
  WorkflowInstanceRow,
  WorkflowNode,
  WorkflowPackage,
} from '@/lib/api/workflow-runtime';

const iso = (offsetMin: number) => new Date(Date.now() + offsetMin * 60_000).toISOString();

function node(partial: Partial<WorkflowNode> & { node_id: string }): WorkflowNode {
  return {
    state: 'PENDING',
    attempts: 0,
    error_code: null,
    bound_ref: null,
    output_ref: null,
    reused: false,
    reuse_evidence: {},
    binding_violations: [],
    ...partial,
  };
}

/** 节点态覆盖九值词表（PENDING/READY/RUNNING/SUCCEEDED/FAILED/BLOCKED/SKIPPED/CANCELLED/STALE）。 */
export const workflowNodesFixture: WorkflowNode[] = [
  node({ node_id: 'extract_sources', state: 'SUCCEEDED', attempts: 1, output_ref: 'ref://ext-1', reused: true, reuse_evidence: { fingerprint: 'fp_e1' } }),
  node({ node_id: 'build_topology', state: 'SUCCEEDED', attempts: 1, output_ref: 'ref://topo-1' }),
  node({ node_id: 'spatial_join', state: 'RUNNING', attempts: 1, bound_ref: 'ref://topo-1' }),
  node({ node_id: 'classify', state: 'READY', attempts: 0 }),
  node({ node_id: 'render_tiles', state: 'PENDING', attempts: 0 }),
  node({ node_id: 'validate_geom', state: 'FAILED', attempts: 3, error_code: 'GEOM_INVALID' }),
  node({ node_id: 'publish_service', state: 'BLOCKED', attempts: 0 }),
  node({ node_id: 'legacy_import', state: 'SKIPPED', attempts: 0 }),
  node({ node_id: 'stale_cache_fill', state: 'STALE', attempts: 1, binding_violations: ['BOUND_REF_GONE'] }),
  node({ node_id: 'cancelled_branch', state: 'CANCELLED', attempts: 0 }),
];

export const workflowInstanceFixture: WorkflowInstance = {
  instance_id: 'wi-ops-1',
  package_id: 'pkg.drainage.analysis',
  package_version: '1.4.2',
  package_fingerprint: 'pkgfp_88aa21bb',
  status: 'running',
  revision: 7,
  cancel_requested: false,
  nodes: workflowNodesFixture,
  counts: {
    PENDING: 1, READY: 1, RUNNING: 1, SUCCEEDED: 2, FAILED: 1,
    BLOCKED: 1, SKIPPED: 1, CANCELLED: 1, STALE: 1,
  },
  decisions: [{ dimension: 'data', target: 'sources', action: 'recompute' }],
  pending_changes: [{ dimension: 'params', target: 'classify', detail: 'threshold 0.4 → 0.45' }],
  error_code: null,
  error_detail: null,
  methodology_family: 'drainage-network-analysis',
  compiler_version: 'wrc-0.9.3',
  explain: {
    why_recomputed: ['data dimension changed: sources refreshed'],
    why_reused: ['build_topology fingerprint unchanged'],
    blocked: [{ node: 'publish_service', codes: ['UPSTREAM_FAILED'] }],
  },
};

export const emptyWorkflowInstanceFixture: WorkflowInstance = {
  ...workflowInstanceFixture,
  instance_id: 'wi-ops-empty',
  status: 'succeeded',
  nodes: [],
  counts: {},
  decisions: [],
  pending_changes: [],
  explain: { why_recomputed: [], why_reused: [], blocked: [] },
};

export const workflowInstanceRowsFixture: WorkflowInstanceRow[] = [
  { instance_id: 'wi-ops-1', package_id: 'pkg.drainage.analysis', status: 'running', revision: 7 },
  { instance_id: 'wi-ops-2', package_id: 'pkg.landcover.stats', status: 'succeeded', revision: 2 },
  { instance_id: 'wi-ops-3', package_id: 'pkg.terrain.mosaic', status: 'failed', revision: 1 },
];

export const emptyWorkflowInstancesFixture: WorkflowInstanceRow[] = [];

export const workflowPackagesFixture: WorkflowPackage[] = [
  { package_id: 'pkg.drainage.analysis', version: '1.4.2', fingerprint: 'pkgfp_88aa21bb', status: 'published', methodology_family: 'drainage-network-analysis' },
  { package_id: 'pkg.landcover.stats', version: '0.9.0', fingerprint: 'pkgfp_33dd44ee', status: 'draft', methodology_family: 'zonal-statistics' },
];

export const emptyWorkflowPackagesFixture: WorkflowPackage[] = [];

const journalEvent = (partial: Partial<RuntimeJournalEvent> & { kind: string; node_id: string | null }): RuntimeJournalEvent => ({
  id: 1,
  from_state: null,
  to_state: null,
  reason: null,
  actor: 'driver',
  attempt: null,
  payload: {},
  created_at: iso(-5),
  ...partial,
});

export const journalEventsFixture: RuntimeJournalEvent[] = [
  journalEvent({ id: 12, kind: 'state_transition', node_id: 'spatial_join', from_state: 'READY', to_state: 'RUNNING', attempt: 1 }),
  journalEvent({ id: 11, kind: 'retry_scheduled', node_id: 'validate_geom', reason: 'GEOM_INVALID', attempt: 2 }),
  journalEvent({ id: 10, kind: 'retry_exhausted', node_id: 'validate_geom', attempt: 3 }),
  journalEvent({ id: 9, kind: 'compensation', node_id: 'build_topology', reason: 'downstream invalid' }),
  journalEvent({ id: 8, kind: 'recovery_orphan_reset', node_id: 'classify', reason: 'lease lost' }),
  journalEvent({ id: 7, kind: 'clone', node_id: null, reason: 'what-if analysis' }),
];

export const recomputePlanFixture: RecomputePlanView = {
  instance_id: 'wi-ops-1',
  stale: 3,
  counts: workflowInstanceFixture.counts,
  decisions: workflowInstanceFixture.decisions,
  style_only: false,
  recompute: ['spatial_join', 'classify', 'render_tiles'],
  reuse: ['extract_sources', 'build_topology'],
  would_mark_stale: ['publish_service'],
  explanations: ['sources refreshed', 'classify params changed'],
  changed_dimensions: ['data', 'params'],
};

export const nodeDetailFixture = {
  instance: {
    instance_id: 'wi-ops-1',
    status: 'running',
    run_lease_owner: 'coord-primary',
    run_lease_expires_at: iso(2),
  },
  node: { ...workflowNodesFixture[2], run_lease_owner: 'coord-primary' },
};

export const debugBundleFixture: DebugBundle = {
  instance: workflowInstanceFixture,
  recent_events: journalEventsFixture,
  children: [
    { instance_id: 'wi-ops-1c1', parent_node_id: 'spatial_join', status: 'running', package_id: 'pkg.drainage.sub' },
  ],
};

/** run 摘要（POST /instances/{id}/run 的 Driver.run 摘要形状）。 */
export const driverRunSummaryFixture = {
  status: 'succeeded',
  states: { spatial_join: 'SUCCEEDED' },
  settled: 4,
  total: 10,
};

/* ------------------------------------------------------------------ */
/* 错误形状（detail = "{CODE}: {detail}" 字符串）                        */
/* ------------------------------------------------------------------ */

export const instanceNotFoundErrorFixture = {
  status: 404,
  body: { detail: 'INSTANCE_NOT_FOUND: instance wi-missing not found' },
};

export const instanceBusyErrorFixture = {
  status: 409,
  body: { detail: 'INSTANCE_BUSY: instance is running' },
};

export const retryExhaustedErrorFixture = {
  status: 409,
  body: { detail: 'RETRY_EXHAUSTED: node validate_geom exhausted retries' },
};
