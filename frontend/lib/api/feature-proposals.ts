/**
 * Feature-proposals API client（G32）—— /chat/sessions/{sid}/feature-proposals/*
 * 的类型化薄封装。ownerToken 经 mapspec session cursor（与 review 同源）；
 * 401 刷新重试由 apiFetch 统一处理。
 */
import { apiFetch } from '@/lib/api/transport';
import type { ApiFetchOptions } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

export type FeatureProposalStatus =
  | 'draft'
  | 'submitted'
  | 'changes_requested'
  | 'approved'
  | 'merging'
  | 'merged'
  | 'rejected'
  | 'withdrawn'
  | 'recovery_required';

export interface ProposalBaseEvidence {
  ref_id: string;
  content_revision: number;
  fingerprint: string;
  snapshot_key: string;
  snapshot_sha256: string;
  snapshot_bytes: number;
  materialized: boolean;
  captured_at: number;
}

export interface ProposalSummary {
  proposal_id: string;
  ref_id: string;
  title: string;
  author_kind: 'agent' | 'user';
  status: FeatureProposalStatus;
  revision: number;
  candidate_digest: string;
  base: ProposalBaseEvidence;
  ops_count: number;
  op_kinds: string[];
  merge: Record<string, unknown> | null;
  has_approval: boolean;
  resolutions_count: number;
  created_at: number;
  updated_at: number;
}

export interface ProposalConflict {
  conflict_id: string;
  kind: string;
  wfid?: string;
  field?: string;
  base?: { presence: string; value?: unknown };
  current?: { presence: string; value?: unknown };
  proposed?: { presence: string; value?: unknown };
  note?: string;
  detail?: Record<string, unknown>;
}

export interface ConflictReport {
  verdict: 'clean' | 'conflicted' | 'blocked';
  conflicts: ProposalConflict[];
  unresolved_conflicts: ProposalConflict[];
  satisfied: Array<{ wfid: string; field: string | null; note?: string }>;
  independent_current_changes: { count: number; deleted: number; created: number; geometry: number };
  identity: { base_materialized?: boolean; current_materialized?: boolean; provable?: boolean };
  current_changed: boolean | null;
}

export interface CandidateDelta {
  wfid: string;
  presence?: 'created' | 'removed';
  fields: Record<string, { before: unknown; after: unknown; before_absent?: boolean }>;
  geometry_before_digest?: string | null;
  geometry_after_digest?: string | null;
  feature_after?: Record<string, unknown>;
  feature_before_summary?: { properties: Record<string, unknown> };
}

export interface ProposalDiff {
  proposal_id: string;
  revision: number;
  base: ProposalBaseEvidence;
  current: { fingerprint: string | null; available: boolean };
  candidate_deltas: CandidateDelta[];
  candidate_delta_count: number;
  page: number;
  page_size: number;
  conflict_report: ConflictReport;
  lineage: Array<Record<string, unknown>>;
}

export interface ProposalDetail extends ProposalSummary {
  ops: Array<Record<string, unknown>>;
  op_seed: string;
  resolutions: Array<Record<string, unknown>>;
  decisions: Array<Record<string, unknown>>;
  comments: Array<Record<string, unknown>>;
  approval: Record<string, unknown> | null;
  withdrawn_at: number | null;
}

export interface MergeReceipt {
  entry_seq: number | null;
  kind: string;
  ref_id: string;
  changed_wfids: string[];
  content_revision_after: number;
  fingerprint_after?: string;
  actor: string;
  origin: string;
  stamp_status?: string;
  mapspec_revision?: number | null;
  created_wfids?: string[];
  retired_wfids?: string[];
  no_changes?: boolean;
}

export interface ResolutionInput {
  conflict_id: string;
  action: 'keep_current' | 'take_proposed' | 'use_new_value';
  new_fields?: Record<string, unknown>;
  new_geometry?: Record<string, unknown>;
}

function session(): { sessionId: string; ownerToken: string | null } | null {
  const { sessionId, ownerToken } = getMapSpecSessionCursor();
  if (sessionId == null) return null;
  return { sessionId, ownerToken: ownerToken ?? null };
}

function base(sessionId: string): string {
  return `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/feature-proposals`;
}

function opts(ownerToken: string | null, init?: Partial<ApiFetchOptions>): ApiFetchOptions {
  return { ...init, ownerToken };
}

export async function listFeatureProposals(status?: string): Promise<ProposalSummary[]> {
  const s = session();
  if (s == null) return [];
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  const data = await apiFetch<{ items?: ProposalSummary[] }>(
    `${base(s.sessionId)}${qs}`, opts(s.ownerToken),
  );
  return data.items ?? [];
}

export async function getFeatureProposalDetail(proposalId: string): Promise<ProposalDetail> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<ProposalDetail>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}`, opts(s.ownerToken),
  );
}

export async function getFeatureProposalDiff(
  proposalId: string, page = 1,
): Promise<ProposalDiff> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<ProposalDiff>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/diff?page=${page}`,
    opts(s.ownerToken),
  );
}

export async function decideFeatureProposal(
  proposalId: string, action: 'submit' | 'approve' | 'request_changes' | 'reject', note?: string,
): Promise<{ proposal: ProposalSummary }> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<{ proposal: ProposalSummary }>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/decisions`,
    opts(s.ownerToken, { method: 'POST', body: JSON.stringify({ action, note }) }),
  );
}

export async function submitFeatureProposalResolutions(
  proposalId: string, expectedRevision: number, resolutions: ResolutionInput[],
): Promise<{ proposal: ProposalSummary; applied: Array<Record<string, unknown>> }> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<{ proposal: ProposalSummary; applied: Array<Record<string, unknown>> }>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/resolutions`,
    opts(s.ownerToken, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision, resolutions }),
    }),
  );
}

export async function rebaseFeatureProposal(
  proposalId: string, expectedRevision: number, apply: boolean,
): Promise<Record<string, unknown>> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<Record<string, unknown>>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/rebase`,
    opts(s.ownerToken, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision, apply }),
    }),
  );
}

export interface MergeFeatureProposalResult {
  status: string;
  proposal_id: string;
  receipt: MergeReceipt;
  proposal?: ProposalSummary;
  already_merged?: boolean;
}

export async function mergeFeatureProposal(proposalId: string): Promise<MergeFeatureProposalResult> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<MergeFeatureProposalResult>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/merge`,
    opts(s.ownerToken, { method: 'POST' }),
  );
}

export async function withdrawFeatureProposal(proposalId: string, note?: string): Promise<Record<string, unknown>> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<Record<string, unknown>>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/withdraw`,
    opts(s.ownerToken, { method: 'POST', body: JSON.stringify({ action: 'withdraw', note }) }),
  );
}

export async function getRecoveryView(proposalId: string): Promise<Record<string, unknown>> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<Record<string, unknown>>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/recovery`,
    opts(s.ownerToken),
  );
}

export async function resolveRecovery(proposalId: string): Promise<Record<string, unknown>> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<Record<string, unknown>>(
    `${base(s.sessionId)}/${encodeURIComponent(proposalId)}/recovery/resolve`,
    opts(s.ownerToken, { method: 'POST' }),
  );
}

export async function createFeatureProposal(input: {
  ref_id: string;
  title?: string;
  ops: Array<Record<string, unknown>>;
  feature_ids?: string[];
  submit?: boolean;
}): Promise<ProposalSummary> {
  const s = session();
  if (s == null) throw new Error('feature-proposals: no session');
  return apiFetch<ProposalSummary>(
    `${base(s.sessionId)}`,
    opts(s.ownerToken, { method: 'POST', body: JSON.stringify(input) }),
  );
}
