/**
 * Upload API - 用户数据上传接口
 *
 * F-FE-3 migration:
 *   - listUploads/getUploadGeojson go through the Fast Path (in-flight
 *     dedup + 5s LRU).
 *   - deleteUpload uses the shared transport (typed ApiError, request id).
 *   - uploadFile keeps XMLHttpRequest because we need upload progress events
 *     (the browser's fetch has no upload progress); however it now:
 *       * sends X-Request-ID
 *       * sets a configurable timeout (default 5 minutes for big raster)
 *       * aborts on AbortSignal
 *       * surfaces the FastAPI `detail` body on error
 */

import { API_BASE } from './config';
import { apiFetch } from './transport';
import { getAccessToken, getRefreshToken, refreshAuthToken } from '../auth/tokenStore';
import { fastGet, invalidateCache } from './get-fast-path';
import type { GeoJSONFeatureCollection } from '@/lib/types';

export interface UploadResponse {
  id: number;
  original_name: string;
  file_type: "vector" | "raster";
  format: string;
  /**
   * #1217（audit3 D-6）：V4 起 CRS 未确认（如未声明的 CSV）时后端如实返回
   * null（crs_source 披露证据来源）—— 此前 TS 声明非空 string，信任该类型
   * 的调用方会在 null 上踩 TypeError。
   */
  crs: string | null;
  geometry_type: string | null;
  feature_count: number;
  bbox: number[] | null;
  file_size: number;
  message?: string;
  // ---- V4 additive（后端 UploadResponse 全量字段镜像）----
  crs_source?: string | null;
  deduplicated?: boolean;
  ignored_files?: string[];
  warnings?: string[];
  meta?: Record<string, unknown> | null;
  session_ref?: string | null;
  profile_summary?: Record<string, unknown> | null;
  quality?: Record<string, unknown> | null;
  ref_registration_error?: string | null;
}

export interface UploadListResponse {
  total: number;
  uploads: UploadResponse[];
}

const UPLOAD_LABEL = 'Upload API error';

/** FastAPI 422 `detail` is an array of `{msg}` objects — don't stringify as [object Object]. */
export function formatUploadErrorDetail(detail: unknown, status: number): string {
  const fallback = `上传失败: ${status}`;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === 'string' && item.trim()) return item;
      if (item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string') {
        const msg = (item as { msg: string }).msg.trim();
        return msg;
      }
      try {
        return JSON.stringify(item);
      } catch {
        return '';
      }
    }).filter((part) => part.length > 0);
    if (parts.length) return parts.join('; ');
  } else if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return fallback;
}

/**
 * 上传文件
 */
export async function uploadFile(
  file: File,
  sessionId?: string,
  onProgress?: (percent: number) => void,
  opts?: { signal?: AbortSignal; timeoutMs?: number; ownerToken?: string | null }
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("files", file);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }

  // XHR keeps the upload progress event that fetch lacks. We add the same
  // request correlation + timeout + abort guarantees the rest of the
  // transport has.
  const requestId =
    globalThis.crypto?.randomUUID?.() ?? `upl-${Date.now().toString(36)}`;
  const timeoutMs = opts?.timeoutMs ?? 5 * 60_000; // 5min default for large raster
  const externalSignal = opts?.signal;

  return new Promise((resolve, reject) => {
    let retried401 = false;

    const openXhr = (): XMLHttpRequest => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/api/v1/upload`);
      xhr.setRequestHeader("X-Request-ID", requestId);
      // #610: 后端 upload 强制 get_current_user（Bearer）。过期后 401 刷新一次再重发。
      const accessToken = getAccessToken();
      if (accessToken) {
        xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
      }
      if (opts?.ownerToken) {
        xhr.setRequestHeader("X-Session-Token", opts.ownerToken);
      }
      xhr.timeout = timeoutMs;
      return xhr;
    };

    let xhr = openXhr();

    const onAbort = () => xhr.abort();
    const cleanup = () => externalSignal?.removeEventListener('abort', onAbort);
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException('The operation was aborted.', 'AbortError'));
    };

    // Wire caller abort → xhr.abort (so the body actually stops streaming).
    if (externalSignal) {
      if (externalSignal.aborted) {
        xhr.abort();
        reject(new DOMException('The operation was aborted.', 'AbortError'));
        return;
      }
      externalSignal.addEventListener('abort', onAbort, { once: true });
    }

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    const rejectHttp = (status: number, responseText: string) => {
      try {
        const err = JSON.parse(responseText) as { detail?: unknown };
        reject(new Error(formatUploadErrorDetail(err.detail, status)));
      } catch {
        reject(new Error(`上传失败: ${status}`));
      }
    };

    xhr.onload = () => {
      if (xhr.status === 401 && !retried401 && getAccessToken() && getRefreshToken()) {
        retried401 = true;
        void refreshAuthToken().then((ok) => {
          if (!ok) {
            cleanup();
            rejectHttp(xhr.status, xhr.responseText);
            return;
          }
          if (externalSignal?.aborted) {
            cleanup();
            reject(new DOMException('The operation was aborted.', 'AbortError'));
            return;
          }
          const previous = xhr;
          const retry = openXhr();
          xhr = retry;
          retry.onabort = previous.onabort;
          retry.onerror = previous.onerror;
          retry.ontimeout = previous.ontimeout;
          retry.upload.onprogress = previous.upload.onprogress;
          retry.onload = () => {
            cleanup();
            if (retry.status >= 200 && retry.status < 300) {
              try {
                const parsed = JSON.parse(retry.responseText);
                invalidateCache('/api/v1/uploads');
                resolve(parsed);
              } catch {
                reject(new Error("解析响应失败"));
              }
              return;
            }
            rejectHttp(retry.status, retry.responseText);
          };
          retry.send(formData);
        });
        return;
      }
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const parsed = JSON.parse(xhr.responseText);
          invalidateCache('/api/v1/uploads');
          resolve(parsed);
        } catch {
          reject(new Error("解析响应失败"));
        }
      } else {
        rejectHttp(xhr.status, xhr.responseText);
      }
    };

    xhr.onerror = () => {
      cleanup();
      if (externalSignal?.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'));
        return;
      }
      reject(new Error("网络错误"));
    };
    xhr.ontimeout = () => { cleanup(); reject(new Error(`上传超时 (${timeoutMs}ms)`)); };
    xhr.send(formData);
  });
}

/**
 * 获取上传列表
 */
export async function listUploads(
  sessionId?: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; ownerToken?: string | null }
): Promise<UploadListResponse> {
  const result = await fastGet<UploadListResponse>('/api/v1/uploads', {
    params: sessionId ? { session_id: sessionId } : undefined,
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: UPLOAD_LABEL,
    ownerToken: opts?.ownerToken,
  });
  return result.data;
}

/**
 * 获取上传的 GeoJSON 数据
 */
export async function getUploadGeojson(
  uploadId: number,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal; ownerToken?: string | null }
): Promise<GeoJSONFeatureCollection> {
  const result = await fastGet<GeoJSONFeatureCollection>(
    `/api/v1/uploads/${uploadId}/geojson`,
    {
      forceRefresh: opts?.forceRefresh,
      signal: opts?.signal,
      label: UPLOAD_LABEL,
      ownerToken: opts?.ownerToken,
    }
  );
  return result.data;
}

/**
 * 删除上传记录
 */
export async function deleteUpload(
  uploadId: number,
  opts?: { signal?: AbortSignal; ownerToken?: string | null }
): Promise<void> {
  await apiFetch<void>(`/api/v1/uploads/${uploadId}`, {
    method: "DELETE",
    parseJson: false,
    signal: opts?.signal,
    label: UPLOAD_LABEL,
    ownerToken: opts?.ownerToken,
  });
  invalidateCache('/api/v1/uploads');
}
