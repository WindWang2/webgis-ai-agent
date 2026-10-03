import { describe, expect, it } from 'vitest';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getPendingPresentation,
  getPendingRemoved,
  markPendingRemoved,
  mergePendingPresentation,
  setMapSpecSessionCursor,
  setMapSpecOwnerToken,
  getMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';

describe('session cursor live MapSpec', () => {
  it('clears committed MapSpec and pending overlay on session change', () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: {},
      layers: [{ id: 'L1', source: 'L1', type: 'circle' }],
    });
    mergePendingPresentation('L1', { visible: false });
    markPendingRemoved('L2');

    setMapSpecSessionCursor('sid-next', 0, null);

    expect(getCommittedMapSpec()).toBeNull();
    expect(getPendingPresentation()).toEqual({});
    expect(getPendingRemoved()).toEqual([]);
  });

  it('F-03: setMapSpecOwnerToken attaches a late owner_token without resetting live state', () => {
    setMapSpecSessionCursor('sid-anon', 3, null);
    commitMapSpecDocument({ version: '1.0', sources: {}, layers: [{ id: 'L1', source: 'L1', type: 'circle' }] });
    mergePendingPresentation('L1', { visible: false });

    setMapSpecOwnerToken('sid-other', 'wrong');
    expect(getMapSpecSessionCursor().ownerToken).toBeNull();

    setMapSpecOwnerToken('sid-anon', 'owner-1');
    expect(getMapSpecSessionCursor()).toMatchObject({ sessionId: 'sid-anon', revision: 3, ownerToken: 'owner-1' });
    expect(getCommittedMapSpec()).not.toBeNull();
    expect(getPendingPresentation()).toEqual({ L1: { visible: false } });
  });
});
