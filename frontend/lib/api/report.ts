/**
 * 报告 API 客户端
 */

import type {
  ReportFormat,
  ReportGenerateRequest,
  ReportGenerateResponse,
  ReportListApiResponse,
  ReportStatusResponse,
  ShareResponse,
  ReportInfo,
} from '../types/report';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

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

  const res = await fetch(`${API_BASE}/reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.message || `生成报告失败: ${res.status}`);
  }

  return res.json();
}

/**
 * 获取报告列表
 */
export async function listReports(
  sessionId?: string,
): Promise<ReportListApiResponse> {
  const params = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const res = await fetch(`${API_BASE}/reports${params}`);

  if (!res.ok) {
    throw new Error(`获取报告列表失败: ${res.status}`);
  }

  return res.json();
}

/**
 * 获取报告状态
 */
export async function getReportStatus(reportId: string): Promise<ReportStatusResponse> {
  const res = await fetch(`${API_BASE}/reports/${reportId}`);

  if (!res.ok) {
    throw new Error(`获取报告状态失败: ${res.status}`);
  }

  return res.json();
}

/**
 * 获取报告下载 URL
 */
export function getReportDownloadUrl(reportId: string): string {
  return `${API_BASE}/reports/${reportId}/download`;
}

/**
 * 创建分享链接
 */
export async function createShareLink(
  reportId: string,
  ttlDays: number = 7,
): Promise<ShareResponse> {
  const res = await fetch(`${API_BASE}/reports/${reportId}/share`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ttl_days: ttlDays }),
  });

  if (!res.ok) {
    throw new Error(`创建分享链接失败: ${res.status}`);
  }

  return res.json();
}

/**
 * 获取分享报告信息
 */
export async function getSharedReportInfo(
  shareCode: string,
): Promise<ReportStatusResponse> {
  const res = await fetch(`${API_BASE}/reports/shared/${shareCode}`);

  if (!res.ok) {
    throw new Error(`获取分享报告失败: ${res.status}`);
  }

  return res.json();
}

/**
 * 获取分享报告查看 URL
 */
export function getSharedReportUrl(shareCode: string): string {
  return `${API_BASE}/reports/shared/${shareCode}/view`;
}
