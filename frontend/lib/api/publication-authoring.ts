/**
 * 只读地图发布作者面 API（04 线 WP5）——与 Pi 工具共用后端 service 的
 * REST 消费面。所有请求携带会话 ownerToken（X-Session-Token）。
 */
import { apiFetch } from '@/lib/api/transport';

export interface PublicationPlanLayer {
  layer_id: string;
  title: string;
  source_id: string;
  source_kind: string;
  projectable: boolean;
  feature_count: number;
  candidate_fields: string[];
  field_whitelist: string[];
  blocker: { reason: string; message: string } | null;
}

export interface PublicationPlanBlocker {
  reason: string;
  message: string;
  layer_id?: string | null;
  source_id?: string | null;
}

export interface PublicationPlan {
  spec_fingerprint: string;
  spec_revision: number | null;
  spec_valid: boolean;
  title: string;
  hydrated_source_count?: number;
  scope: { crs: string; geometry_precision: number; time_start: string | null; time_end: string | null };
  layers: PublicationPlanLayer[];
  blockers: PublicationPlanBlocker[];
  publishable?: boolean;
}

export interface PublicationView {
  id: string;
  product_id: string;
  version: number;
  session_id: string;
  title: string;
  status: 'draft' | 'published' | 'revoked';
  access_policy: 'private' | 'link';
  has_share_link: boolean;
  share_expires_at: string | null;
  spec_fingerprint: string;
  materialized: boolean;
  layer_count: number;
  blockers: PublicationPlanBlocker[];
  qa: { spec_validity: string; checks: [string, string][] };
  snapshot_fingerprint: string | null;
  versions?: { id: string; version: number; status: string; spec_fingerprint: string; published_at: string | null }[];
}

export interface PublicationMetaInput {
  title?: string;
  purpose?: string;
  attribution?: string;
  license?: string;
  missing_values_note?: string;
}

export interface PublicationDraftInput extends PublicationMetaInput {
  selected_layer_ids?: string[];
  field_whitelists?: Record<string, string[]>;
  alt_text?: string;
  palette?: string;
  base_publication_id?: string;
}

const envelope = async <T>(promise: Promise<{ data: T }>): Promise<T> => (await promise).data;

export function previewPublication(
  sessionId: string,
  body: { selected_layer_ids?: string[]; field_whitelists?: Record<string, string[]> },
  ownerToken?: string | null,
): Promise<PublicationPlan> {
  return envelope(apiFetch<{ data: PublicationPlan }>('/api/v1/publications/preview', {
    method: 'POST',
    body: { session_id: sessionId, ...body },
    ownerToken,
    label: 'Publication preview',
  }));
}

export function createPublicationDraft(
  sessionId: string,
  body: PublicationDraftInput,
  ownerToken?: string | null,
): Promise<PublicationView> {
  return envelope(apiFetch<{ data: PublicationView }>('/api/v1/publications', {
    method: 'POST',
    body: { session_id: sessionId, ...body },
    ownerToken,
    label: 'Publication draft',
  }));
}

export function publishPublication(
  publicationId: string,
  confirm: boolean,
  ownerToken?: string | null,
): Promise<{ publication: PublicationView; job: { status: string; job_id: string; message: string; idempotent_reuse: boolean } }> {
  return envelope(apiFetch<{ data: { publication: PublicationView; job: { status: string; job_id: string; message: string; idempotent_reuse: boolean } } }>(
    `/api/v1/publications/${encodeURIComponent(publicationId)}/publish`,
    {
      method: 'POST',
      body: { confirm },
      ownerToken,
      label: 'Publication publish',
    },
  ));
}

export function mintPublicationLink(
  publicationId: string,
  ttlDays: number | null,
  ownerToken?: string | null,
): Promise<{ share_code: string; share_url: string; expires_at: string | null }> {
  return envelope(apiFetch<{ data: { share_code: string; share_url: string; expires_at: string | null } }>(
    `/api/v1/publications/${encodeURIComponent(publicationId)}/link`,
    { method: 'POST', body: { ttl_days: ttlDays }, ownerToken, label: 'Publication link' },
  ));
}

export function revokePublicationLink(publicationId: string, ownerToken?: string | null): Promise<void> {
  return envelope(apiFetch<{ data: void }>(
    `/api/v1/publications/${encodeURIComponent(publicationId)}/link`,
    { method: 'DELETE', ownerToken, label: 'Publication link revoke' },
  ));
}

export function revokePublication(publicationId: string, ownerToken?: string | null): Promise<void> {
  return envelope(apiFetch<{ data: void }>(
    `/api/v1/publications/${encodeURIComponent(publicationId)}/revoke`,
    { method: 'POST', body: {}, ownerToken, label: 'Publication revoke' },
  ));
}

export function getPublication(publicationId: string, ownerToken?: string | null): Promise<PublicationView> {
  return envelope(apiFetch<{ data: PublicationView }>(
    `/api/v1/publications/${encodeURIComponent(publicationId)}`,
    { method: 'GET', ownerToken, label: 'Publication get' },
  ));
}

export async function listSessionPublications(
  sessionId: string,
  ownerToken?: string | null,
): Promise<PublicationView[]> {
  const body = await envelope(apiFetch<{ data: { publications: PublicationView[] } }>(
    `/api/v1/publications/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'GET', ownerToken, label: 'Publication list' },
  ));
  return body.publications;
}
