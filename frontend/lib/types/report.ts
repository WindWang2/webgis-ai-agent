/**
 * 报告功能 - 类型定义
 */

export type ReportFormat = 'pdf' | 'html' | 'markdown' | 'md';

export type ReportStatus = 'pending' | 'generating' | 'completed' | 'failed';

export interface ReportInfo {
  id: string;
  session_id: string;
  title: string;
  format: ReportFormat;
  status: ReportStatus;
  file_size?: number;
  share_code?: string;
  share_expires_at?: string;
  error_message?: string;
  created_at?: string;
  download_url?: string | null;
}

export interface ReportGenerateRequest {
  session_id: string;
  format: ReportFormat;
  title?: string;
}

export interface ReportListResponse {
  total: number;
  items: ReportInfo[];
}

export interface ShareInfo {
  share_code: string;
  share_url: string;
  expires_at: string;
  ttl_days: number;
}

/** ApiResponse wrappers */
export interface ReportGenerateResponse {
  code: string;
  success: boolean;
  message: string;
  data: ReportInfo;
}

export interface ReportListApiResponse {
  code: string;
  success: boolean;
  message: string;
  data: ReportListResponse;
}

export interface ReportStatusResponse {
  code: string;
  success: boolean;
  message: string;
  data: ReportInfo;
}

export interface ShareResponse {
  code: string;
  success: boolean;
  message: string;
  data: ShareInfo;
}
