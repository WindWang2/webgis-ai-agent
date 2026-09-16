/**
 * Review store + protocol 'review' 事件解析测试（ADR-0201）。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import {
  getReviewState,
  reviewApplyBusEvent,
  reviewSetDetail,
  reviewSetMergeOutcome,
  reviewSetProposals,
  reviewSetSelected,
  reviewSetSession,
  reviewSetStatus,
  resetReviewStoreForTests,
} from './store';
import { parseEnvelope, parseReviewEvent } from '@/lib/collab/protocol';

describe('review store', () => {
  beforeEach(() => resetReviewStoreForTests());

  it('setProposals 归一化 + 有界', () => {
    reviewSetProposals('s1', [
      {
        proposal_id: 'rp_1', title: 't', status: 'submitted', risk: 'high',
        base_revision: 3, author: { actor_id: 'u1', actor_kind: 'user', role: 'editor' },
        updated_at: '2026-09-17T00:00:00+00:00',
      },
      { garbage: true },
      null,
    ]);
    const s = getReviewState();
    expect(s.status).toBe('ready');
    expect(s.proposals).toHaveLength(1);
    expect(s.proposals[0].risk).toBe('high');
    expect(s.proposals[0].author.actor_kind).toBe('user');
  });

  it('selected + detail + merge outcome 投影', () => {
    reviewSetSelected('rp_1');
    expect(getReviewState().selectedId).toBe('rp_1');
    reviewSetDetail({
      proposal: { proposal_id: 'rp_1', title: 't' },
      anchor_states: { rc_1: 'stale' },
      conflict: true,
      current_revision: 9,
      base_revision: 7,
      policy: { snapshot: {}, satisfied: false, counted_approvals: 0, blocking_reasons: ['x'] },
    });
    expect(getReviewState().detail?.conflict).toBe(true);
    reviewSetMergeOutcome({ ok: false, conflict: true, interleaved: false, rolled_back: false, failure: 'base_revision_drift' });
    expect(getReviewState().detail?.merge_outcome?.failure).toBe('base_revision_drift');
  });

  it('bus 事件回声 + 会话重置', () => {
    reviewSetSession('s1');
    reviewApplyBusEvent({ proposalId: 'rp_1', event: 'state', status: 'merged', actor: 'u2' });
    expect(getReviewState().lastEventAt).not.toBeNull();
    reviewSetStatus('error');
    resetReviewStoreForTests();
    expect(getReviewState().status).toBe('idle');
    expect(getReviewState().proposals).toHaveLength(0);
  });
});

describe('collab protocol review kind', () => {
  it('parseEnvelope 接受 review kind（additive）', () => {
    const env = parseEnvelope({
      v: 1, kind: 'review', sid: 's1', seq: 5, ts: '2026-09-17T00:00:00+00:00',
      data: { proposalId: 'rp_1', event: 'state', status: 'approved', actor: 'u2' },
    });
    expect(env).not.toBeNull();
    expect(env?.kind).toBe('review');
  });

  it('parseReviewEvent 有界归一化；未知 event 丢弃', () => {
    const env = parseEnvelope({
      v: 1, kind: 'review', sid: 's1', seq: 5, ts: 'x',
      data: { proposalId: 'rp_1', event: 'nonsense', status: 'x'.repeat(100), actor: 'a'.repeat(500) },
    });
    expect(parseReviewEvent(env!)).toBeNull();
    const ok = parseEnvelope({
      v: 1, kind: 'review', sid: 's1', seq: 6, ts: 'x',
      data: { proposalId: 'p'.repeat(999), event: 'comment', status: 'submitted', actor: 'u9' },
    });
    const parsed = parseReviewEvent(ok!);
    expect(parsed?.proposalId.length).toBe(64);
    expect(parsed?.actor).toBe('u9');
  });

  it('非法信封仍然拒收（v≠1 / 未知 kind 不受影响）', () => {
    expect(parseEnvelope({ v: 2, kind: 'review', sid: 's', seq: 1, ts: 'x', data: {} })).toBeNull();
    expect(parseEnvelope({ v: 1, kind: 'mystery', sid: 's', seq: 1, ts: 'x', data: {} })).toBeNull();
  });
});
