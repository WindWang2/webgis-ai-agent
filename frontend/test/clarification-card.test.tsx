/**
 * ClarificationCard 组件测试（方向 3 / ADR-0222）。
 *
 * 覆盖：选项渲染与选择、自由文本、waive（仅 can_waive）、提交回调形状、
 * 状态机（submitting/submitted/stale 禁用）、键盘提交、ARIA radiogroup。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ClarificationCard } from '@/components/chat/clarification-card'
import { I18nProvider } from '@/lib/i18n/i18n-provider'
import type { ClarificationCardState } from '@/lib/types/clarification'

function state(overrides: Partial<ClarificationCardState> = {}): ClarificationCardState {
  return {
    envelope: {
      schema_version: 'clarification_envelope.v1',
      request_id: 'clar-req-test-0001-aaaaaaaaaaaa',
      session_id: 'sess-1',
      document_id: 'req-x',
      requirement_revision: 3,
      raised_turn_id: 'turn-1',
      questions: [
        {
          code: 'measure_missing_for_statistics',
          context_key: 'k1',
          path: 'measures',
          question_zh: '要统计的指标是什么？',
          question_en: 'Which measure?',
          options: [
            { value: 'count', label_zh: '数量', label_en: 'Count', is_default: true },
            { value: 'density', label_zh: '密度', label_en: 'Density', is_default: false },
          ],
          allow_free_text: true,
          can_waive: false,
          default_value: '',
          default_rationale: '',
          reason_summary: '',
        },
      ],
      defaultable: false,
    },
    status: 'open',
    ...overrides,
  } as ClarificationCardState
}

function mount(s: ClarificationCardState, onSubmit = vi.fn(), onRefresh?: () => void) {
  return render(
    <I18nProvider>
      <ClarificationCard state={s} onSubmit={onSubmit} onRefresh={onRefresh} />
    </I18nProvider>,
  )
}

describe('ClarificationCard', () => {
  it('渲染问题与选项（radiogroup 语义 + 推荐标记）', () => {
    mount(state())
    expect(screen.getByText('要统计的指标是什么？')).toBeTruthy()
    expect(screen.getByRole('radiogroup', { name: '要统计的指标是什么？' })).toBeTruthy()
    expect(screen.getByLabelText(/数量/)).toBeTruthy()
    expect(screen.getByText('推荐')).toBeTruthy()   // is_default 标记
  })

  it('未作答时提交禁用；选择后可提交并回传结构化应答', () => {
    const onSubmit = vi.fn()
    mount(state(), onSubmit)
    const submit = screen.getByRole('button', { name: /提交答案/ })
    expect((submit as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByLabelText(/密度/))
    expect((submit as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(submit)
    expect(onSubmit).toHaveBeenCalledWith({
      requestId: 'clar-req-test-0001-aaaaaaaaaaaa',
      answers: [{ context_key: 'k1', value: 'density' }],
      waiveKeys: [],
    })
  })

  it('自由文本输入可作为答案；Enter 提交', () => {
    const onSubmit = vi.fn()
    mount(state(), onSubmit)
    const input = screen.getByPlaceholderText(/或输入其他答案/)
    fireEvent.change(input, { target: { value: '人均学校数' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledWith({
      requestId: 'clar-req-test-0001-aaaaaaaaaaaa',
      answers: [{ context_key: 'k1', value: '人均学校数' }],
      waiveKeys: [],
    })
  })

  it('can_waive 的问题出现默认值豁免；勾选后走 waive 通道', () => {
    const s = state()
    s.envelope.questions[0].can_waive = true
    s.envelope.questions[0].default_value = 'pdf'
    const onSubmit = vi.fn()
    mount(s, onSubmit)
    const waive = screen.getByLabelText(/使用推荐默认值/)
    fireEvent.click(waive)
    fireEvent.click(screen.getByRole('button', { name: /提交答案/ }))
    expect(onSubmit).toHaveBeenCalledWith({
      requestId: s.envelope.request_id,
      answers: [],
      waiveKeys: ['k1'],
    })
  })

  it('不可 waive 的问题不出现豁免选项', () => {
    mount(state())
    expect(screen.queryByLabelText(/使用推荐默认值/)).toBeNull()
  })

  it('submitting 状态全部输入禁用', () => {
    mount(state({ status: 'submitting' }))
    expect((screen.getByLabelText(/数量/) as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByPlaceholderText(/或输入其他答案/) as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: /提交中/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('submitted 状态锁定并回显答案；不再出现提交按钮', () => {
    mount(state({
      status: 'submitted',
      submittedAnswers: { k1: 'density' },
    }))
    expect(screen.getByText(/已提交/)).toBeTruthy()
    expect(screen.getByText(/density/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /提交答案/ })).toBeNull()
  })

  it('stale 状态展示过期提示与刷新入口', () => {
    const onRefresh = vi.fn()
    mount(state({ status: 'stale' }), vi.fn(), onRefresh)
    expect(screen.getByText('已过期')).toBeTruthy()   // 状态 pill（精确匹配）
    expect(screen.getByText(/此卡片已过期/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /重新获取/ }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('error 状态有 alert 语义的失败提示', () => {
    mount(state({ status: 'error', errorCode: 'clarification_answer_invalid' }))
    expect(screen.getByRole('alert').textContent).toContain('提交失败')
  })

  it('≤2 问题渲染；卡片 data-* 暴露 request_id/status（E2E 锚）', () => {
    const s = state()
    s.envelope.questions.push({
      ...s.envelope.questions[0],
      code: 'aoi_unresolved',
      context_key: 'k2',
      question_zh: '范围？',
    })
    const { container } = mount(s)
    const el = container.querySelector('[data-testid="clarification-card"]') as HTMLElement
    expect(el).toBeTruthy()
    expect(el.dataset.requestId).toBe(s.envelope.request_id)
    expect(el.dataset.status).toBe('open')
    expect(container.querySelectorAll('fieldset')).toHaveLength(2)
  })
})
