import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportPreview } from './report-preview'

// Mock the API functions
vi.mock('@/lib/api/report', () => ({
  getReportDownloadUrl: vi.fn(() => 'http://localhost:8000/api/v1/reports/test-id/download'),
  getSharedReportUrl: vi.fn((code: string) => `http://localhost:8000/api/v1/reports/shared/${code}`),
}))

describe('ReportPreview', () => {
  it('shows empty state when no report', () => {
    render(<ReportPreview report={null} />)
    expect(screen.getByText('暂无报告')).toBeInTheDocument()
  })

  it('shows processing state when report is pending', () => {
    const report = {
      report_id: 'test-id',
      task_id: 1,
      format: 'pdf' as const,
      status: 'pending' as const,
      download_url: '/api/v1/reports/test-id/download',
      created_at: new Date().toISOString(),
    }
    render(<ReportPreview report={report} />)
    expect(screen.getByText('报告生成中...')).toBeInTheDocument()
  })

  it('shows download link for non-HTML formats', () => {
    const report = {
      report_id: 'test-id',
      task_id: 1,
      format: 'pdf' as const,
      status: 'completed' as const,
      download_url: '/api/v1/reports/test-id/download',
      created_at: new Date().toISOString(),
    }
    render(<ReportPreview report={report} />)
    expect(screen.getByText('当前格式不支持在线预览')).toBeInTheDocument()
    expect(screen.getByText('下载查看')).toBeInTheDocument()
  })

  it('shows iframe for HTML format', () => {
    const report = {
      report_id: 'test-id',
      task_id: 1,
      format: 'html' as const,
      status: 'completed' as const,
      download_url: '/api/v1/reports/test-id/download',
      created_at: new Date().toISOString(),
    }
    render(<ReportPreview report={report} />)
    expect(screen.getByTitle('报告预览')).toBeInTheDocument()
  })
})
