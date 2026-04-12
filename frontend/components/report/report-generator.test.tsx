import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportGenerator } from './report-generator'

// Mock the API functions
vi.mock('@/lib/api/report', () => ({
  generateReport: vi.fn(),
  getReportDownloadUrl: vi.fn(() => 'http://localhost:8000/api/v1/reports/test-id/download'),
  createShareLink: vi.fn(),
}))

describe('ReportGenerator', () => {
  it('renders without crashing', () => {
    render(<ReportGenerator sessionId={null} />)
    expect(screen.getByText('导出格式')).toBeInTheDocument()
  })

  it('shows disabled button when sessionId is null', () => {
    render(<ReportGenerator sessionId={null} />)
    const button = screen.getByRole('button', { name: /生成报告/i })
    expect(button).toBeDisabled()
  })

  it('shows format selector', () => {
    render(<ReportGenerator sessionId="test-session" />)
    expect(screen.getByRole('combobox')).toBeInTheDocument()
  })

  it('shows enabled button when sessionId is provided', () => {
    render(<ReportGenerator sessionId="test-session" />)
    const button = screen.getByRole('button', { name: /生成报告/i })
    expect(button).not.toBeDisabled()
  })
})
