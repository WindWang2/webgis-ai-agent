import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportGenerator } from './report-generator'

// Mock the API functions
vi.mock('@/lib/api/report', () => ({
  generateReport: vi.fn(),
  pollReportStatus: vi.fn(),
  getReportDownloadUrl: vi.fn(() => 'http://localhost:8000/api/v1/reports/test-id/download'),
  createShareLink: vi.fn(),
}))

describe('ReportGenerator', () => {
  it('renders without crashing', () => {
    render(<ReportGenerator taskId={null} />)
    expect(screen.getByText('导出格式')).toBeInTheDocument()
  })

  it('shows disabled button when taskId is null', () => {
    render(<ReportGenerator taskId={null} />)
    const button = screen.getByRole('button', { name: /生成报告/i })
    expect(button).toBeDisabled()
  })

  it('shows format selector', () => {
    render(<ReportGenerator taskId={1} />)
    expect(screen.getByRole('combobox')).toBeInTheDocument()
  })

  it('shows enabled button when taskId is provided', () => {
    render(<ReportGenerator taskId={1} />)
    const button = screen.getByRole('button', { name: /生成报告/i })
    expect(button).not.toBeDisabled()
  })
})
