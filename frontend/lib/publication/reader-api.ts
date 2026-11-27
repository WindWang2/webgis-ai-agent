/**
 * 只读地图发布读者 API（04 线）——公开 share_code 端点的唯一前端消费面。
 * 全部请求 skipAuth：读者不持登录态，凭 URL 中的 share_code 访问。
 */
import { apiFetch } from '@/lib/api/transport';
import type {
  ReaderDoc,
  ReaderFeaturePage,
  ReaderMapPayload,
  ReaderSummary,
} from './reader-types';

export function sharedBase(code: string): string {
  // code 形态校验与后端一致（token_urlsafe 字符集）；防路径注入。
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(code)) {
    throw new Error('invalid share code');
  }
  return `/api/v1/publications/shared/${code}`;
}

export async function fetchReaderDoc(code: string, signal?: AbortSignal): Promise<ReaderDoc> {
  const body = await apiFetch<{ data: ReaderDoc }>(sharedBase(code), {
    method: 'GET',
    skipAuth: true,
    cache: 'no-store',
    signal,
    label: 'Publication reader',
  });
  return body.data;
}

export async function fetchReaderMap(
  code: string,
  signal?: AbortSignal,
): Promise<ReaderMapPayload> {
  const body = await apiFetch<{ data: ReaderMapPayload }>(`${sharedBase(code)}/map`, {
    method: 'GET',
    skipAuth: true,
    cache: 'no-store',
    signal,
    label: 'Publication reader map',
  });
  return body.data;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && `${value}`.length > 0) {
      search.set(key, `${value}`);
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

export async function fetchReaderFeatures(
  code: string,
  layerId: string,
  opts: {
    limit?: number;
    cursor?: string | null;
    where?: unknown;
    bbox?: [number, number, number, number] | null;
    fields?: string[] | null;
    signal?: AbortSignal;
  } = {},
): Promise<ReaderFeaturePage> {
  const body = await apiFetch<{ data: ReaderFeaturePage }>(
    `${sharedBase(code)}/layers/${encodeURIComponent(layerId)}/features${query({
      limit: opts.limit,
      cursor: opts.cursor ?? undefined,
      where: opts.where ? JSON.stringify(opts.where) : undefined,
      bbox: opts.bbox ? opts.bbox.join(',') : undefined,
      fields: opts.fields ? opts.fields.join(',') : undefined,
    })}`,
    { method: 'GET', skipAuth: true, cache: 'no-store', signal: opts.signal, label: 'Publication features' },
  );
  return body.data;
}

export async function fetchReaderSummary(
  code: string,
  layerId: string,
  field: string,
  opts: { where?: unknown; bbox?: [number, number, number, number] | null; signal?: AbortSignal } = {},
): Promise<ReaderSummary> {
  const body = await apiFetch<{ data: ReaderSummary }>(
    `${sharedBase(code)}/summary${query({
      layer_id: layerId,
      field,
      where: opts.where ? JSON.stringify(opts.where) : undefined,
      bbox: opts.bbox ? opts.bbox.join(',') : undefined,
    })}`,
    { method: 'GET', skipAuth: true, cache: 'no-store', signal: opts.signal, label: 'Publication summary' },
  );
  return body.data;
}
