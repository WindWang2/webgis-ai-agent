import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportPreview } from './report-preview'

// Mock the API functions
vi.mock('@/lib/api/report', () => ({
  getReportDownloadUrl: vi.fn(() => 'http://localhost:8000/api/v1/reports/test-id/download'),
  getSharedReportUrl: vi.fn((code: string) => `http://localhost:8000/api/v1/reports/shared/${code}`),
}))

// #515: the HTML preview now fetches the report blob through the transport
// (iframe src must carry Bearer → object URL). Stub it in tests.
const apiFetchBlobMock = vi.fn()
vi.mock('@/lib/api/transport', () => ({
  apiFetchBlob: (...args: unknown[]) => apiFetchBlobMock(...args),
}))

function htmlBlob(): Blob {
  return new Blob(['<html><body>report</body></html>'], { type: 'text/html' })
}

beforeEach(() => {
  apiFetchBlobMock.mockReset()
  apiFetchBlobMock.mockResolvedValue({ blob: htmlBlob(), filename: 'report_test.html' })
})

describe('ReportPreview', () => {
  it('shows empty state when no report', () => {
    render(<ReportPreview report={null} />)
    expect(screen.getByText('暂无报告')).toBeInTheDocument()
  })

  it('shows processing state when report is pending', () => {
    const report = {
      id: 'test-id',
      session_id: 'session-1',
      title: 'Test Report',
      format: 'pdf' as const,
      status: 'pending' as const,
      created_at: new Date().toISOString(),
    }
    render(<ReportPreview report={report} />)
    expect(screen.getByText('报告生成中...')).toBeInTheDocument()
  })

  it('shows download link for non-HTML formats', () => {
    const report = {
      id: 'test-id',
      session_id: 'session-1',
      title: 'Test Report',
      format: 'pdf' as const,
      status: 'completed' as const,
      download_url: '/api/v1/reports/test-id/download',
      created_at: new Date().toISOString(),
    }
    render(<ReportPreview report={report} />)
    expect(screen.getByText('当前格式不支持在线预览')).toBeInTheDocument()
    expect(screen.getByText('下载查看')).toBeInTheDocument()
  })

  it('shows iframe for HTML format', async () => {
    const report = {
      id: 'test-id',
      session_id: 'session-1',
      title: 'Test Report',
      format: 'html' as const,
      status: 'completed' as const,
      download_url: '/api/v1/reports/test-id/download',
      created_at: new Date().toISOString(),
    }
    render(<ReportPreview report={report} />)
    // 等待 blob 拉取完成并渲染出 iframe（objectURL 驱动）。
    await screen.findByTitle('报告预览')
    expect(apiFetchBlobMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/reports/test-id/download'
    )
  })
})
