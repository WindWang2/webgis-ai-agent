/**
 * T005 报告功能 - 类型定义
 */

export type ReportFormat = 'pdf' | 'html' | 'markdown' | 'md';

export type ReportStatus = 'pending' | 'processing' | 'completed' | 'failed';

export interface ReportGenerateRequest {
  task_id: number;
  format: ReportFormat;
  include_map_screenshot?: boolean;
}

export interface ReportInfo {
  report_id: string;
  task_id: number;
  format: ReportFormat;
  status: ReportStatus;
  download_url: string;
  created_at: string;
  file_size?: number;
  error_message?: string;
}

export interface ShareInfo {
  share_code: string;
  share_url: string;
  expire_at: number;
  ttl_days: number;
}

export interface ReportGenerateResponse {
  code: string;
  success: boolean;
  message: string;
  data: ReportInfo;
}

export interface ReportStatusResponse {
  code: string;
  success: boolean;
  data: ReportInfo;
}

export interface ShareResponse {
  code: string;
  success: boolean;
  data: ShareInfo;
}
