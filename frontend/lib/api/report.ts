/**
 * 报告 API 客户端
 *
 * F-FE-3 migration: previously raw fetch + plain Error with status only.
 * Now routes through the shared transport (typed ApiError, abort, timeout,
 * request id). GETs go through the Fast Path (in-flight dedup + 5s LRU).
 */

import { API_BASE } from './config';
import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

import type {
  ReportFormat,
  ReportGenerateRequest,
  ReportGenerateResponse,
  ReportListApiResponse,
  ReportStatusResponse,
  ShareResponse,
} from '../types/report';

const REPORT_LABEL = 'Report API error';

/**
 * 生成报告（同步等待结果）
 */
export async function generateReport(
  sessionId: string,
  format: ReportFormat = 'pdf',
  title?: string,
): Promise<ReportGenerateResponse> {
  const body: ReportGenerateRequest = {
    session_id: sessionId,
    format,
    ...(title ? { title } : {}),
  };
  const out = await apiFetch<ReportGenerateResponse>('/api/v1/reports', {
    method: 'POST',
    body,
    // WeasyPrint render can take longer than the default 30s.
    timeoutMs: 90_000,
    label: REPORT_LABEL,
  });
  invalidateCache('/api/v1/reports');
  return out;
}

/**
 * 获取报告列表
 */
export async function listReports(
  sessionId?: string,
  opts?: { forceRefresh?: boolean; signal?: AbortSignal }
): Promise<ReportListApiResponse> {
  const result = await fastGet<ReportListApiResponse>('/api/v1/reports', {
    params: sessionId ? { session_id: sessionId } : undefined,
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: REPORT_LABEL,
  });
  return result.data;
}

/**
 * 获取报告状态
 */
export async function getReportStatus(
  reportId: string,
  opts?: { signal?: AbortSignal }
): Promise<ReportStatusResponse> {
  const result = await fastGet<ReportStatusResponse>(`/api/v1/reports/${reportId}`, {
    signal: opts?.signal,
    label: REPORT_LABEL,
  });
  return result.data;
}

/**
 * 获取报告下载 URL
 */
export function getReportDownloadUrl(reportId: string): string {
  return `${API_BASE}/api/v1/reports/${reportId}/download`;
}

/**
 * 创建分享链接
 */
export async function createShareLink(
  reportId: string,
  ttlDays: number = 7,
): Promise<ShareResponse> {
  return apiFetch<ShareResponse>(`/api/v1/reports/${reportId}/share`, {
    method: 'POST',
    body: { ttl_days: ttlDays },
    label: REPORT_LABEL,
  });
}

/**
 * 获取分享报告信息
 */
export async function getSharedReportInfo(
  shareCode: string,
  opts?: { signal?: AbortSignal }
): Promise<ReportStatusResponse> {
  return apiFetch<ReportStatusResponse>(
    `/api/v1/reports/shared/${encodeURIComponent(shareCode)}`,
    {
      signal: opts?.signal,
      label: REPORT_LABEL,
    }
  );
}

/**
 * 获取分享报告查看 URL
 */
export function getSharedReportUrl(shareCode: string): string {
  return `${API_BASE}/api/v1/reports/shared/${shareCode}/view`;
}
