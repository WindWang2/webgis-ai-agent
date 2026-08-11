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
import { fastGet, invalidateCache } from './get-fast-path';
import type { GeoJSONFeatureCollection } from '@/lib/types';

export interface UploadResponse {
  id: number;
  original_name: string;
  file_type: "vector" | "raster";
  format: string;
  crs: string;
  geometry_type: string | null;
  feature_count: number;
  bbox: number[] | null;
  file_size: number;
  message?: string;
}

export interface UploadListResponse {
  total: number;
  uploads: UploadResponse[];
}

const UPLOAD_LABEL = 'Upload API error';

/**
 * 上传文件
 */
export async function uploadFile(
  file: File,
  sessionId?: string,
  onProgress?: (percent: number) => void,
  opts?: { signal?: AbortSignal; timeoutMs?: number }
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
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/v1/upload`);
    xhr.setRequestHeader("X-Request-ID", requestId);
    xhr.timeout = timeoutMs;

    // Wire caller abort → xhr.abort (so the body actually stops streaming).
    if (externalSignal) {
      if (externalSignal.aborted) {
        xhr.abort();
        reject(new DOMException('The operation was aborted.', 'AbortError'));
        return;
      }
      externalSignal.addEventListener('abort', () => xhr.abort(), { once: true });
    }

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const parsed = JSON.parse(xhr.responseText);
          invalidateCache('/api/v1/uploads');
          resolve(parsed);
        } catch {
          reject(new Error("解析响应失败"));
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText);
          reject(new Error(err.detail || `上传失败: ${xhr.status}`));
        } catch {
          reject(new Error(`上传失败: ${xhr.status}`));
        }
      }
    };

    xhr.onerror = () => {
      if (externalSignal?.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'));
        return;
      }
      reject(new Error("网络错误"));
    };
    xhr.ontimeout = () => reject(new Error(`上传超时 (${timeoutMs}ms)`));
    xhr.send(formData);
  });
}

/**
 * 获取上传列表
 */
export async function listUploads(
  sessionId?: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal }
): Promise<UploadListResponse> {
  const result = await fastGet<UploadListResponse>('/api/v1/uploads', {
    params: sessionId ? { session_id: sessionId } : undefined,
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: UPLOAD_LABEL,
  });
  return result.data;
}

/**
 * 获取上传的 GeoJSON 数据
 */
export async function getUploadGeojson(
  uploadId: number,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal }
): Promise<GeoJSONFeatureCollection> {
  const result = await fastGet<GeoJSONFeatureCollection>(
    `/api/v1/uploads/${uploadId}/geojson`,
    {
      forceRefresh: opts?.forceRefresh,
      signal: opts?.signal,
      label: UPLOAD_LABEL,
    }
  );
  return result.data;
}

/**
 * 删除上传记录
 */
export async function deleteUpload(
  uploadId: number,
  opts?: { signal?: AbortSignal }
): Promise<void> {
  await apiFetch<void>(`/api/v1/uploads/${uploadId}`, {
    method: "DELETE",
    parseJson: false,
    signal: opts?.signal,
    label: UPLOAD_LABEL,
  });
  invalidateCache('/api/v1/uploads');
}
