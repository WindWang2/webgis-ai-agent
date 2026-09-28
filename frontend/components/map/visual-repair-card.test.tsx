import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { VisualRepairCard } from './visual-repair-card'
import { ApiError } from '@/lib/api/transport'

const PLAN_OK = {
  session_id: 's1',
  proposable: true,
  proposal_id: 'vrepair-abc12345-7',
  base_revision: 7,
  ops: [
    {
      op: 'label_layout',
      layer_ids: ['L1'],
      severity: 'warning',
      rationale: '注记与 L1 overlap 重叠',
      touches_locked: true,
    },
    {
      op: 'opacity',
      layer_ids: ['L2'],
      severity: 'warning',
      rationale: 'occluded 遮挡',
      touches_locked: false,
    },
  ],
  finding_ids: ['visual:visual_label_collision:fp'],
  stale_count: 0,
}

function fetchJson(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('VisualRepairCard', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders nothing without a session', () => {
    const { container } = render(<VisualRepairCard sessionId={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('plans on open and renders the structured ops diff', async () => {
    fetchMock.mockResolvedValue(fetchJson(PLAN_OK))
    render(<VisualRepairCard sessionId="s1" />)
    fireEvent.click(screen.getByTestId('visual-repair-open'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-ops')).toBeInTheDocument())
    const ops = screen.getAllByTestId('visual-repair-op')
    expect(ops).toHaveLength(2)
    // 锁交集知情批准披露。
    expect(screen.getByTestId('visual-repair-touches-locked')).toBeInTheDocument()
    // plan 是零突变预览：只有 plan 调用，没有 apply。
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toContain('/visual-repairs/plan')
  })

  it('approves with the structural gate fields (approved + expected_revision)', async () => {
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith('/plan')
        ? fetchJson(PLAN_OK)
        : fetchJson({ applied: true, duplicate: false, mutation_revision: 8 }))
    render(<VisualRepairCard sessionId="s1" />)
    fireEvent.click(screen.getByTestId('visual-repair-open'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-approve')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('visual-repair-approve'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-applied')).toBeInTheDocument())
    const [, init] = fetchMock.mock.calls.find(
      (c: unknown[]) => String(c[0]).endsWith('/apply'),
    ) as [string, RequestInit]
    const body = JSON.parse(String(init.body))
    expect(body).toEqual({
      proposal_id: 'vrepair-abc12345-7',
      approved: true,
      expected_revision: 7,
    })
  })

  it('reject calls the reject endpoint and closes the card', async () => {
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith('/plan')
        ? fetchJson(PLAN_OK)
        : fetchJson({ rejected: true }))
    render(<VisualRepairCard sessionId="s1" />)
    fireEvent.click(screen.getByTestId('visual-repair-open'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-reject')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('visual-repair-reject'))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c: unknown[]) => String(c[0]).endsWith('/reject'))).toBe(true))
    // 卡片收起，回到入口按钮（拒绝记忆已入账）。
    await waitFor(() => expect(screen.getByTestId('visual-repair-open')).toBeInTheDocument())
  })

  it('renders honest non-proposable reasons', async () => {
    fetchMock.mockResolvedValue(fetchJson({
      session_id: 's1', proposable: false,
      reason: 'stale_findings', stale_count: 3,
    }))
    render(<VisualRepairCard sessionId="s1" />)
    fireEvent.click(screen.getByTestId('visual-repair-open'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-nonproposable')).toBeInTheDocument())
    expect(screen.getByTestId('visual-repair-nonproposable').textContent).toContain('3')
    expect(screen.queryByTestId('visual-repair-approve')).not.toBeInTheDocument()
  })

  it('surfaces the convergence hard stop honestly (not as failure)', async () => {
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith('/plan')
        ? fetchJson(PLAN_OK)
        : fetchJson({
            applied: false, hard_stop: true,
            reason: 'convergence_exhausted', correction_hint: '请人工研判。',
          }))
    render(<VisualRepairCard sessionId="s1" />)
    fireEvent.click(screen.getByTestId('visual-repair-open'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-approve')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('visual-repair-approve'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-hard-stop')).toBeInTheDocument())
  })

  it('shows an error state when plan transport fails', async () => {
    fetchMock.mockRejectedValue(new ApiError(503, 'busy'))
    render(<VisualRepairCard sessionId="s1" />)
    fireEvent.click(screen.getByTestId('visual-repair-open'))
    await waitFor(() => expect(screen.getByTestId('visual-repair-error')).toBeInTheDocument())
  })
})
