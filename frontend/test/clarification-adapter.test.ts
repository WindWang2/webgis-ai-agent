/**
 * clarificationDecision 有界校验表测试（方向 3 / ADR-0222）。
 *
 * fail-closed 原则：缺 request_id / revision 越界 / questions 空或 >2 /
 * 缺 context_key → null（不渲染半张卡）。
 */
import { describe, it, expect } from 'vitest'
import { clarificationDecision } from '@/lib/chat/adapters'

const base = {
  schema_version: 'clarification_envelope.v1',
  request_id: 'clar-req-abc123def456-0123456789ab',
  session_id: 'sess-1',
  document_id: 'req-abc123def456',
  requirement_revision: 3,
  raised_turn_id: 'turn-xyz',
  defaultable: false,
}

const question = {
  code: 'aoi_unresolved',
  context_key: 'k1',
  path: 'aoi.name',
  question_zh: '分析范围是哪个城市？',
  question_en: 'Which city?',
  options: [
    { value: '成都', label_zh: '成都', label_en: 'Chengdu', is_default: false },
  ],
  allow_free_text: true,
  can_waive: false,
  default_value: '',
  default_rationale: '',
  reason_summary: 'aoi_unresolved @ aoi.name',
}

describe('clarificationDecision', () => {
  it('合法载荷 → open 卡片状态', () => {
    const state = clarificationDecision({ ...base, questions: [question] })
    expect(state).not.toBeNull()
    expect(state!.status).toBe('open')
    expect(state!.envelope.request_id).toBe(base.request_id)
    expect(state!.envelope.questions).toHaveLength(1)
    expect(state!.envelope.questions[0].options[0].value).toBe('成都')
  })

  it('缺 request_id / revision → null', () => {
    expect(clarificationDecision({ ...base, request_id: '', questions: [question] })).toBeNull()
    expect(clarificationDecision({ ...base, requirement_revision: 0, questions: [question] })).toBeNull()
    expect(clarificationDecision({ ...base, requirement_revision: 1.5, questions: [question] })).toBeNull()
  })

  it('request_id 超长 → null（bounded）', () => {
    expect(
      clarificationDecision({ ...base, request_id: 'x'.repeat(65), questions: [question] }),
    ).toBeNull()
  })

  it('questions 空 / >2 / 缺 context_key → null', () => {
    expect(clarificationDecision({ ...base, questions: [] })).toBeNull()
    expect(
      clarificationDecision({ ...base, questions: [question, question, question] }),
    ).toBeNull()
    expect(
      clarificationDecision({ ...base, questions: [{ ...question, context_key: '' }] }),
    ).toBeNull()
  })

  it('options 截断到 4 且过滤无 value 项', () => {
    const opts = Array.from({ length: 6 }, (_, i) => ({
      value: i === 4 ? '' : `v${i}`,
      label_zh: `o${i}`,
      label_en: '',
      is_default: i === 0,
    }))
    const state = clarificationDecision({
      ...base,
      questions: [{ ...question, options: opts as never }],
    })
    expect(state!.envelope.questions[0].options).toHaveLength(4)
    expect(state!.envelope.questions[0].options.every((o) => o.value)).toBe(true)
  })

  it('非对象载荷 → null', () => {
    expect(clarificationDecision(null)).toBeNull()
    expect(clarificationDecision('nope')).toBeNull()
    expect(clarificationDecision(undefined)).toBeNull()
  })
})
