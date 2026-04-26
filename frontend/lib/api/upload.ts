/**
 * Upload API - 用户数据上传接口
 */

import { API_BASE } from './config';

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

/**
 * 上传文件
 */
export async function uploadFile(
  file: File,
  sessionId?: string,
  onProgress?: (percent: number) => void
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("files", file);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/v1/upload`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
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

    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(formData);
  });
}

/**
 * 获取上传列表
 */
export async function listUploads(sessionId?: string): Promise<UploadListResponse> {
  const params = sessionId ? `?session_id=${sessionId}` : "";
  const res = await fetch(`${API_BASE}/api/v1/uploads${params}`);
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}

/**
 * 获取上传的 GeoJSON 数据
 */
export async function getUploadGeojson(
  uploadId: number
): Promise<GeoJSON.FeatureCollection> {
  const res = await fetch(`${API_BASE}/api/v1/uploads/${uploadId}/geojson`);
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
  return res.json();
}

/**
 * 删除上传记录
 */
export async function deleteUpload(uploadId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/uploads/${uploadId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`API Error: ${res.status}`);
}
