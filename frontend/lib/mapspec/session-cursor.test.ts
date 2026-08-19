import { describe, expect, it } from 'vitest';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getPendingPresentation,
  getPendingRemoved,
  markPendingRemoved,
  mergePendingPresentation,
  setMapSpecSessionCursor,
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
});
