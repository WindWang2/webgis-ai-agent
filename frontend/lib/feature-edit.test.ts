/**
 * Feature-edit 前端接线测试（/goal 03）：
 * - stepResultDecision 的 featureEditProposal 分支（最小契约收窄）
 * - message reducer 挂载/状态迁移
 * - api 客户端路径/鉴权（fetch 打桩）
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { stepResultDecision } from '@/lib/chat/adapters';
import {
  attachFeatureEditProposal,
  setFeatureEditProposalStatus,
} from '@/lib/chat/message-reducer';
import {
  decideFeatureEditPlan,
  undoFeatureEdit,
} from '@/lib/api/feature-edit';
import type { ChatStreamMessage } from '@/lib/chat/message-reducer';
import type { FeatureEditProposalState } from '@/lib/types/feature-edit';

const ctx = {
  sessionId: 'sess-1',
  accentColor: '#3b82f6',
  now: 1_700_000_000_000,
  labelFor: (tool: string) => tool,
} as Parameters<typeof stepResultDecision>[1];

function msg(id: string): ChatStreamMessage {
  return { id, role: 'assistant', content: 'x' } as unknown as ChatStreamMessage;
}

describe('stepResultDecision · featureEditProposal', () => {
  it('preview 工具成功结果 → 提案载荷（plan_id/ref/ops/preview）', () => {
    const decision = stepResultDecision(
      {
        tool: 'webgis_feature_edit_preview',
        result: {
          success: true,
          plan_id: 'fep-1',
          ref_id: 'ref:data-x',
          safe: true,
          n_targets: 3,
          ops: [{ op: 'set_fields', fields: ['type'] }],
          preview: [{ wfid: 'wf1-1', fields: { type: { before: '中学', after: '小学' } } }],
          expected_content_revision: 6,
          expires_in_s: 600,
        },
      } as never,
      ctx,
    );
    expect(decision.featureEditProposal).toMatchObject({
      plan_id: 'fep-1',
      ref_id: 'ref:data-x',
      status: 'pending',
      safe: true,
      n_targets: 3,
      expected_content_revision: 6,
    });
  });

  it('失败/无 plan_id 的预览不挂卡', () => {
    const decision = stepResultDecision(
      {
        tool: 'webgis_feature_edit_preview',
        result: { success: false, code: 'ENUM_VIOLATION', findings: [] },
      } as never,
      ctx,
    );
    expect(decision.featureEditProposal).toBeNull();
  });

  it('非 preview 工具不误挂', () => {
    const decision = stepResultDecision(
      { tool: 'ingest_dataset', result: { success: true, plan_id: 'x' } } as never,
      ctx,
    );
    expect(decision.featureEditProposal).toBeNull();
  });

  it('commit 工具成功回执 → featureEditCommit（数据刷新 + 卡片推进依据）', () => {
    const decision = stepResultDecision(
      {
        tool: 'webgis_feature_edit_commit',
        result: {
          success: true,
          plan_id: 'fep-9',
          ref_id: 'ref:data-x',
          entry_seq: 3,
          content_revision_after: 8,
          stamp_status: 'deferred',
        },
      } as never,
      ctx,
    );
    expect(decision.featureEditCommit).toMatchObject({
      plan_id: 'fep-9',
      ref_id: 'ref:data-x',
      content_revision_after: 8,
    });
  });

  it('commit 被拒（CONFIRMATION_REQUIRED）不产生刷新决策', () => {
    const decision = stepResultDecision(
      {
        tool: 'webgis_feature_edit_commit',
        result: { success: false, code: 'CONFIRMATION_REQUIRED', plan_id: 'fep-9' },
      } as never,
      ctx,
    );
    expect(decision.featureEditCommit).toBeNull();
  });
});

describe('message reducer · featureEditProposal', () => {
  const proposal: FeatureEditProposalState = {
    plan_id: 'fep-1',
    ref_id: 'ref:data-x',
    status: 'pending',
    safe: true,
    n_targets: 1,
    ops: [],
    schema_ops: [],
    preview: [],
    validation: [],
    expected_content_revision: 2,
  };

  it('挂载幂等（同 plan_id 重放不重复）', () => {
    const prev = [msg('m1')];
    const once = attachFeatureEditProposal(prev, { messageId: 'm1', proposal });
    const twice = attachFeatureEditProposal(once, { messageId: 'm1', proposal });
    expect(twice).toBe(once);
  });

  it('状态迁移只影响目标 plan', () => {
    const prev = attachFeatureEditProposal([msg('m1')], { messageId: 'm1', proposal });
    const next = setFeatureEditProposalStatus(prev, {
      planId: 'fep-1',
      status: 'applied',
      receipt: { content_revision_after: 3 },
    });
    expect(next[0].featureEditProposal?.status).toBe('applied');
    expect(next[0].featureEditProposal?.receipt?.content_revision_after).toBe(3);
  });
});

describe('feature-edit api client', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('decision POST 到正确路径并携带 owner token 头', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'applied', receipt: { entry_seq: 1 } }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const result = await decideFeatureEditPlan('sess-1', 'fep-1', 'approve', 'tok-1');
    expect(result.status).toBe('applied');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/sessions/sess-1/feature-edits/plans/fep-1/decision');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['X-Session-Token']).toBe('tok-1');
  });

  it('undo POST 409 时抛出带 status 的错误（卡片收敛依据）', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: 'stale_revision' } }), { status: 409 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const err = await undoFeatureEdit('sess-1', 'ref:data-x').catch((e: unknown) => e);
    const apiErr = err as { status?: number; body?: { detail?: { code?: string }; code?: string } };
    expect(apiErr.status).toBe(409);
    // FastAPI detail 信封：body.detail.code（或扁平 code）
    expect(apiErr.body?.detail?.code ?? apiErr.body?.code).toBe('stale_revision');
  });
});
