import { beforeEach, describe, expect, it } from 'vitest';
import {
  useVisualAttachStore,
  type VisualAttachmentDraft,
} from './attach-store';

function draft(evidenceId: string): VisualAttachmentDraft {
  return {
    evidenceId,
    sourceType: 'reference_upload',
    label: `${evidenceId}.png`,
  };
}

describe('visual attachment addDraft feedback', () => {
  beforeEach(() => {
    useVisualAttachStore.setState({ sessionId: null, drafts: [] });
  });

  it('returns success and appends the draft for an armed session', () => {
    useVisualAttachStore.getState().begin('s1');

    expect(useVisualAttachStore.getState().addDraft(draft('e-1'))).toEqual({ ok: true });
    expect(useVisualAttachStore.getState().drafts.map((d) => d.evidenceId)).toEqual(['e-1']);
  });

  it('reports an unarmed session without retaining the draft', () => {
    expect(useVisualAttachStore.getState().addDraft(draft('e-1'))).toEqual({
      ok: false,
      reason: 'session-unarmed',
    });
    expect(useVisualAttachStore.getState().drafts).toEqual([]);
  });

  it('reports a duplicate evidence id without appending another draft', () => {
    useVisualAttachStore.getState().begin('s1');
    useVisualAttachStore.getState().addDraft(draft('e-1'));

    expect(useVisualAttachStore.getState().addDraft(draft('e-1'))).toEqual({
      ok: false,
      reason: 'duplicate',
    });
    expect(useVisualAttachStore.getState().drafts.map((d) => d.evidenceId)).toEqual(['e-1']);
  });

  it('reports the cap without discarding the caller draft silently', () => {
    useVisualAttachStore.setState({
      sessionId: 's1',
      drafts: ['e-1', 'e-2', 'e-3', 'e-4'].map(draft),
    });

    expect(useVisualAttachStore.getState().addDraft(draft('e-5'))).toEqual({
      ok: false,
      reason: 'full',
    });
    expect(useVisualAttachStore.getState().drafts.map((d) => d.evidenceId)).toEqual([
      'e-1', 'e-2', 'e-3', 'e-4',
    ]);
  });
});
