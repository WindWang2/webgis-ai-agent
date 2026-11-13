import { describe, expect, it } from 'vitest';
import {
  defaultPrintLayout, draftFieldErrors, initialPrintLayoutState,
  isDraftDirty, reducePrintLayout,
} from './contracts';

describe('G34 print layout state machine', () => {
  it('default plan validates and is A4 landscape in millimetres', () => {
    const plan = defaultPrintLayout();
    expect(plan.version).toBe('print_layout.v1');
    expect(plan.paper.width_mm).toBe(297);
    expect(plan.paper.height_mm).toBe(210);
    expect(draftFieldErrors(plan)).toEqual([]);
  });

  it('rejects unknown versions, out-of-paper frames and invalid legend columns', () => {
    const plan = defaultPrintLayout();
    const bad = {
      ...plan,
      version: 'print_layout.v9' as never,
      components: [
        { ...plan.components[0], frame: { x: 290, y: 10, width: 100, height: 18 } },
        { ...plan.components[2], legend: { ...plan.components[2].legend!, columns: 9 } },
      ],
    };
    const errors = draftFieldErrors(bad);
    expect(errors).toContain('unknown_version');
    expect(errors.some((e) => e.startsWith('outside_paper:'))).toBe(true);
    expect(errors.some((e) => e.startsWith('invalid_legend_columns:'))).toBe(true);
  });

  it('zoom changes display only, never the stored millimetre frame', () => {
    const plan = defaultPrintLayout();
    let state = reducePrintLayout(initialPrintLayoutState(), {
      type: 'load-ok', generation: 0, plan, revision: 7,
    });
    const before = JSON.stringify(state.draft);
    state = reducePrintLayout(state, { type: 'zoom', zoom: 3 });
    expect(state.zoom).toBe(3);
    expect(JSON.stringify(state.draft)).toBe(before);
  });

  it('generation guard drops a stale preview from a previous page', () => {
    let state = reducePrintLayout(initialPrintLayoutState(), {
      type: 'load-ok', generation: 0, plan: defaultPrintLayout(), revision: 1,
    });
    state = reducePrintLayout(state, { type: 'page-switch', pageId: 'p2' });
    expect(state.generation).toBe(1);
    const stale = reducePrintLayout(state, {
      type: 'preview-ok', generation: 0, svg: '<svg>old</svg>',
      receipt: {}, fingerprint: 'old-page',
    });
    expect(stale.previewSvg).toBeNull();
    expect(stale.previewFingerprint).toBeNull();
    const fresh = reducePrintLayout(state, {
      type: 'preview-ok', generation: 1, svg: '<svg>new</svg>',
      receipt: {}, fingerprint: 'current-page',
    });
    expect(fresh.previewSvg).toContain('new');
    expect(fresh.previewFingerprint).toBe('current-page');
  });

  it('draft is dirty until cancelled or committed', () => {
    const plan = defaultPrintLayout();
    let state = reducePrintLayout(initialPrintLayoutState(), {
      type: 'load-ok', generation: 0, plan, revision: 3,
    });
    expect(isDraftDirty(state.committed, state.draft)).toBe(false);
    state = reducePrintLayout(state, {
      type: 'patch-component', id: 'title',
      patch: { frame: { x: 15, y: 10, width: 180, height: 24 } },
    });
    expect(state.phase).toBe('draft');
    expect(isDraftDirty(state.committed, state.draft)).toBe(true);
    state = reducePrintLayout(state, { type: 'restore-committed' });
    expect(isDraftDirty(state.committed, state.draft)).toBe(false);
  });

  it('cancel yields an explicit terminal state, never a fake completed', () => {
    const state = reducePrintLayout(initialPrintLayoutState(), { type: 'cancel' });
    expect(state.phase).toBe('cancelled');
    expect(state.errors).toContain('cancelled');
    expect(state.busy).toBe(false);
  });
});
