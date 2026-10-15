/**
 * 澄清卡片消息 reducer 测试（方向 3 / ADR-0222）。
 *
 * 纪律与 message-reducer 既有测试一致：目标不存在原样返回；幂等重放
 * 不产生新引用；恢复挂卡有界。
 */
import { describe, it, expect } from 'vitest'
import {
  attachClarification,
  setClarificationStatus,
  replaceClarificationEnvelope,
  attachRestoredClarification,
  type ChatStreamMessage,
} from '@/lib/chat/message-reducer'
import type { ClarificationCardState, ClarificationEnvelope } from '@/lib/types/clarification'

function envelope(requestId: string, revision = 3): ClarificationEnvelope {
  return {
    schema_version: 'clarification_envelope.v1',
    request_id: requestId,
    session_id: 'sess-1',
    document_id: 'req-x',
    requirement_revision: revision,
    raised_turn_id: 'turn-1',
    questions: [
      {
        code: 'aoi_unresolved',
        context_key: 'k1',
        path: 'aoi.name',
        question_zh: '范围？',
        question_en: 'Where?',
        options: [],
        allow_free_text: true,
        can_waive: false,
        default_value: '',
        default_rationale: '',
        reason_summary: '',
      },
    ],
    defaultable: false,
  }
}

function card(requestId: string, status: ClarificationCardState['status'] = 'open'): ClarificationCardState {
  return { envelope: envelope(requestId), status }
}

function msgs(...withCard: ClarificationCardState[]): ChatStreamMessage[] {
  return [
    { id: 'm1', role: 'user', content: 'hi', timestamp: 1 },
    {
      id: 'm2', role: 'assistant', content: '', timestamp: 2,
      ...(withCard.length ? { clarification: withCard[0] } : {}),
    },
  ]
}

describe('attachClarification', () => {
  it('挂载到目标消息；同 request_id 同状态幂等（引用恒定）', () => {
    const prev = msgs()
    const next = attachClarification(prev, { messageId: 'm2', clarification: card('r1') })
    expect(next[1].clarification?.envelope.request_id).toBe('r1')
    const again = attachClarification(next, { messageId: 'm2', clarification: card('r1') })
    expect(again).toBe(next)
  })

  it('目标不存在原样返回', () => {
    const prev = msgs()
    expect(attachClarification(prev, { messageId: 'nope', clarification: card('r1') })).toBe(prev)
  })
})

describe('setClarificationStatus', () => {
  it('按 request_id 迁移状态并带提交回显', () => {
    const prev = msgs(card('r1'))
    const next = setClarificationStatus(prev, {
      requestId: 'r1',
      status: 'submitted',
      submittedAnswers: { k1: '成都' },
    })
    expect(next[1].clarification?.status).toBe('submitted')
    expect(next[1].clarification?.submittedAnswers?.k1).toBe('成都')
  })

  it('其他 request_id 的卡片不受影响', () => {
    const prev = msgs(card('r1'))
    const next = setClarificationStatus(prev, { requestId: 'other', status: 'error' })
    expect(next[1].clarification?.status).toBe('open')
  })
})

describe('replaceClarificationEnvelope（stale/409 恢复）', () => {
  it('envelope 缺席 → open/submitting/error 卡片转 stale', () => {
    const prev = msgs(card('r1', 'open'))
    const next = replaceClarificationEnvelope(prev, { envelope: undefined })
    expect(next[1].clarification?.status).toBe('stale')
  })

  it('submitted 卡片不受缺席影响', () => {
    const prev = msgs(card('r1', 'submitted'))
    const next = replaceClarificationEnvelope(prev, { envelope: undefined })
    expect(next[1].clarification?.status).toBe('submitted')
  })

  it('新 envelope 替换旧 open 卡片（不同 request_id）', () => {
    const prev = msgs(card('r1', 'open'))
    const next = replaceClarificationEnvelope(prev, { envelope: envelope('r2', 7) })
    expect(next[1].clarification?.envelope.request_id).toBe('r2')
    expect(next[1].clarification?.status).toBe('open')
  })

  it('同 request_id 复位 open（重试后刷新）', () => {
    const prev = msgs(card('r1', 'error'))
    const next = replaceClarificationEnvelope(prev, { envelope: envelope('r1', 3) })
    expect(next[1].clarification?.status).toBe('open')
  })

  it('无既有卡片 → 挂到最后一条助手消息', () => {
    const prev = msgs()
    const next = replaceClarificationEnvelope(prev, { envelope: envelope('r9') })
    expect(next[1].clarification?.envelope.request_id).toBe('r9')
  })

  it('多卡时单 live 不变式：精确匹配优先，其余非 submitted 转 stale（review N4）', () => {
    const prev: ChatStreamMessage[] = [
      { id: 'm1', role: 'user', content: 'hi', timestamp: 1 },
      { id: 'm2', role: 'assistant', content: '', timestamp: 2, clarification: card('old-1', 'open') },
      { id: 'm3', role: 'user', content: 'again', timestamp: 3 },
      { id: 'm4', role: 'assistant', content: '', timestamp: 4, clarification: card('old-2', 'error') },
    ]
    // 新 envelope 与 m2 的 request_id 匹配 → m2 复位 open；m4（不匹配）转 stale
    const next = replaceClarificationEnvelope(prev, { envelope: envelope('old-1', 9) })
    expect(next[1].clarification?.status).toBe('open')
    expect(next[1].clarification?.envelope.requirement_revision).toBe(9)
    expect(next[3].clarification?.status).toBe('stale')
    const liveCards = next.filter(
      (m) => m.clarification && m.clarification.status !== 'submitted' && m.clarification.status !== 'stale',
    )
    expect(liveCards).toHaveLength(1)
    // 无精确匹配：第一张非 submitted 被替换，其余转 stale
    const next2 = replaceClarificationEnvelope(prev, { envelope: envelope('brand-new') })
    expect(next2[1].clarification?.envelope.request_id).toBe('brand-new')
    expect(next2[1].clarification?.status).toBe('open')
    expect(next2[3].clarification?.status).toBe('stale')
  })
})

describe('attachRestoredClarification（会话恢复）', () => {
  it('无同 request_id 卡片 → 追加合成助手消息', () => {
    const prev = msgs()
    const next = attachRestoredClarification(prev, { envelope: envelope('r1'), now: 123 })
    expect(next).toHaveLength(3)
    expect(next[2].role).toBe('assistant')
    expect(next[2].clarification?.envelope.request_id).toBe('r1')
    expect(next[2].id).toBe('clarification-r1')
  })

  it('同 request_id 已在 → 幂等跳过', () => {
    const prev = msgs(card('r1'))
    const next = attachRestoredClarification(prev, { envelope: envelope('r1'), now: 123 })
    expect(next).toBe(prev)
  })
})
