/**
 * T005 报告功能 - API 客户端
 */

import type {
  ReportFormat,
  ReportGenerateRequest,
  ReportGenerateResponse,
  ReportStatusResponse,
  ShareResponse,
  ReportInfo,
} from '../types/report';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://192.168.193.121:8000/api/v1';

/**
 * 生成报告
 */
export async function generateReport(
  taskId: number,
  format: ReportFormat = 'pdf',
  includeMapScreenshot: boolean = true
): Promise<ReportGenerateResponse> {
  const res = await fetch(`${API_BASE}/reports/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      task_id: taskId,
      format,
      include_map_screenshot: includeMapScreenshot,
    } as ReportGenerateRequest),
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.message || `生成报告失败: ${res.status}`);
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
 * 获取报告下载URL
 */
export function getReportDownloadUrl(reportId: string): string {
  return `${API_BASE}/reports/${reportId}/download`;
}

/**
 * 创建分享链接
 */
export async function createShareLink(
  reportId: string,
  ttlDays: number = 7
): Promise<ShareResponse> {
  const res = await fetch(`${API_BASE}/reports/${reportId}/share?ttl_days=${ttlDays}`, {
    method: 'POST',
  });

  if (!res.ok) {
    throw new Error(`创建分享链接失败: ${res.status}`);
  }

  return res.json();
}

/**
 * 获取分享报告的URL
 */
export function getSharedReportUrl(shareCode: string): string {
  return `${API_BASE}/reports/shared/${shareCode}`;
}

/**
 * 轮询等待报告生成完成
 */
export async function pollReportStatus(
  reportId: string,
  maxAttempts: number = 30,
  intervalMs: number = 1000
): Promise<ReportInfo> {
  for (let i = 0; i < maxAttempts; i++) {
    const response = await getReportStatus(reportId);
    const { status } = response.data;

    if (status === 'completed' || status === 'failed') {
      return response.data;
    }

    // 等待后继续轮询
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }

  throw new Error('报告生成超时');
}
