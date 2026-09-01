/**
 * Map components manager — pure action derivation (Workspace V2 / Goal C4).
 *
 * Invariants:
 * - every action is expressed as a patch_component CAS payload on the SAME
 *   component truth (MapSpec placement) — the manager holds no state;
 * - the manageable/multi-instance vocabularies mirror the backend registry
 *   seeds (cardinality=multiple: legend family, chart_panel, annotation);
 * - zIndex stays inside the bounded placement range (≤200).
 */
import { describe, expect, it } from 'vitest';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { resolveMapComponent } from '@/lib/map-components/resolve-components';
import {
  MANAGEABLE_TYPES,
  MULTIPLE_INSTANCE_TYPES,
  availableActions,
  bringToFrontPatch,
  manageableComponents,
  maxFloatingZIndex,
  resetPositionPatch,
  toggleCollapsePatch,
  toggleVisibilityPatch,
} from './manager';

function component(overrides: Partial<MapSpecComponent> = {}): MapSpecComponent {
  return {
    id: 'chart-panel',
    type: 'chart_panel',
    enabled: true,
    placement: undefined,
    options: { chartRef: 'ref:chart-1' },
    ...overrides,
  } as MapSpecComponent;
}

describe('manager vocabularies', () => {
  it('multiple-instance set mirrors the backend registry seeds', () => {
    expect([...MULTIPLE_INSTANCE_TYPES].sort()).toEqual(
      ['annotation', 'categorical_legend', 'chart_panel', 'continuous_colorbar', 'legend', 'table_panel'].sort(),
    );
  });

  it('manageable set covers chrome instances, excludes canvas-level decorations', () => {
    expect(MANAGEABLE_TYPES.has('title')).toBe(true);
    expect(MANAGEABLE_TYPES.has('map_border')).toBe(false);
    expect(MANAGEABLE_TYPES.has('graticule')).toBe(false);
    expect(MANAGEABLE_TYPES.has('basemap')).toBe(false);
  });

  it('manageableComponents filters by vocabulary keeping declaration order', () => {
    const resolved = [
      resolveMapComponent(component({ id: 't', type: 'title' })),
      resolveMapComponent(component({ id: 'g', type: 'graticule' })),
      resolveMapComponent(component({ id: 'c', type: 'chart_panel' })),
    ];
    expect(manageableComponents(resolved).map((c) => c.id)).toEqual(['t', 'c']);
  });
});

describe('availableActions', () => {
  it('enabled chart panel can hide/collapse but not reset position while anchored', () => {
    const actions = availableActions(resolveMapComponent(component()));
    expect(actions).toMatchObject({ show: false, hide: true, collapse: true, expand: false, resetPosition: false, dock: true });
  });

  it('floating panel gains reset/bring-to-front', () => {
    const resolved = resolveMapComponent(
      component({ placement: { mode: 'floating', x: 10, y: 10, width: 300, height: 200, zIndex: 5 } }),
    );
    const actions = availableActions(resolved);
    expect(actions.resetPosition).toBe(true);
    expect(actions.bringToFront).toBe(true);
  });

  it('disabled instance can only be shown', () => {
    const actions = availableActions(resolveMapComponent(component({ enabled: false })));
    expect(actions).toMatchObject({ show: true, hide: false, collapse: false, expand: false, dock: true });
  });

  it('non-panel chrome (title) has no collapse/dock semantics', () => {
    const actions = availableActions(resolveMapComponent(component({ id: 'title', type: 'title' })));
    expect(actions.collapse).toBe(false);
    expect(actions.expand).toBe(false);
    expect(actions.dock).toBe(false);
  });
});

describe('action patches', () => {
  it('visibility patch flips enabled', () => {
    expect(toggleVisibilityPatch(resolveMapComponent(component()))).toEqual({ enabled: false });
  });

  it('collapse patch preserves floating coordinates and mode', () => {
    const resolved = resolveMapComponent(
      component({ placement: { mode: 'floating', x: 12, y: 34, width: 300, height: 200 } }),
    );
    expect(toggleCollapsePatch(resolved)).toEqual({
      placement: { mode: 'floating', x: 12, y: 34, width: 300, height: 200, collapsed: true },
    });
  });

  it('collapse patch on anchored panel reconstructs effective anchor placement', () => {
    const resolved = resolveMapComponent(component()); // chart_panel default top-left
    expect(toggleCollapsePatch(resolved)).toEqual({
      placement: { mode: 'anchor', anchor: 'top-left', collapsed: true },
    });
  });

  it('reset position clears floating coords back to the type default slot', () => {
    const resolved = resolveMapComponent(
      component({ placement: { mode: 'floating', x: 12, y: 34, width: 300, height: 200, zIndex: 9 } }),
    );
    expect(resetPositionPatch(resolved)).toEqual({
      placement: { mode: 'anchor', anchor: 'top-left' },
    });
  });

  it('legend reset returns to bottom-left (type default)', () => {
    const resolved = resolveMapComponent(
      component({ id: 'legend-main', type: 'legend', placement: { mode: 'floating', x: 1, y: 1 } }),
    );
    expect(resetPositionPatch(resolved).placement).toEqual({ mode: 'anchor', anchor: 'bottom-left' });
  });

  it('bring to front is one above current max and clamped to the bounded range', () => {
    const resolved = resolveMapComponent(
      component({ placement: { mode: 'floating', x: 0, y: 0, zIndex: 3 } }),
    );
    expect(bringToFrontPatch(resolved, 7).placement).toMatchObject({ mode: 'floating', zIndex: 8 });
    expect(bringToFrontPatch(resolved, 200).placement).toMatchObject({ zIndex: 200 });
    expect(bringToFrontPatch(resolved, -1).placement).toMatchObject({ zIndex: 0 + 1 });
  });

  it('maxFloatingZIndex reads only floating instances', () => {
    const resolved = [
      resolveMapComponent(component({ id: 'a', placement: { mode: 'floating', x: 0, y: 0, zIndex: 4 } })),
      resolveMapComponent(component({ id: 'b' })), // anchored
      resolveMapComponent(component({ id: 'c', placement: { mode: 'floating', x: 0, y: 0, zIndex: 11 } })),
    ];
    expect(maxFloatingZIndex(resolved)).toBe(11);
  });
});
