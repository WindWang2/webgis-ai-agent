// P3 修复回归：raster commit 的客户端幂等键必须走共享 crypto
// newMutationId（UUID v4）。旧实现用 Date.now() 毫秒 + 6 位 base36
// Math.random 后缀——同毫秒后缀碰撞会被服务端 c:<id> 判重静默丢弃
// （第二次提交被当成重放，静默丢失）。
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/transport', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '@/lib/api/transport';
import { commitExpression } from './api';
import type { RasterExpressionDraft } from './contracts';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const ctx = { sessionId: 'session-raster-1', ownerToken: 'owner-test' };
const draft: RasterExpressionDraft = {
  schema_version: 'raster_expression.v1',
  mode: 'single',
  bands: [1],
  autoStretch: true,
  stretchMin: 0,
  stretchMax: 1,
  perChannel: [[0, 1]],
  colormap: 'viridis',
  opacity: 1,
  resampling: 'nearest',
  classification: 'continuous',
};

function sentMutationId(callIndex: number): string {
  const init = vi.mocked(apiFetch).mock.calls[callIndex][1] as
    { body?: Record<string, unknown> } | undefined;
  return String(init?.body?.client_mutation_id);
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset().mockResolvedValue({ success: true } as never);
});

describe('commitExpression client_mutation_id', () => {
  it('mints a crypto UUID v4 id, not a millisecond timestamp + random suffix', async () => {
    await commitExpression(ctx, 'ref-1', 'layer-1', draft, 7);
    expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(1);
    expect(sentMutationId(0)).toMatch(UUID_V4);
  });

  it('mints distinct ids for back-to-back commits inside one millisecond', async () => {
    // 同一毫秒内连点两次提交：幂等键不得共享时间戳成分而碰撞。
    const ids = new Set<string>();
    for (let i = 0; i < 5; i += 1) {
      await commitExpression(ctx, 'ref-1', 'layer-1', draft, 7);
      ids.add(sentMutationId(i));
    }
    expect(ids.size).toBe(5);
  });
});
