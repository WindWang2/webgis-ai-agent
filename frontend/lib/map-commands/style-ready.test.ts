import { describe, it, expect, vi } from 'vitest';
import { whenStyleReady } from './style-ready';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';

/**
 * Style gate for imperative map mutations. react-map-gl yields the instance
 * before the async style load finishes; addSource in that window throws
 * "Style is not done loading." These tests pin the deferral contract:
 * synchronous when loaded, deferred via load/styledata when not, cancellable.
 */

describe('whenStyleReady', () => {
  it('fires synchronously when the style is already loaded', () => {
    const map = makeMockMaplibreMap(); // styleLoaded defaults to true
    const onReady = vi.fn();
    const cancel = whenStyleReady(map as any, onReady);

    expect(onReady).toHaveBeenCalledTimes(1);
    expect(map.once).not.toHaveBeenCalled();
    expect(() => cancel()).not.toThrow();
  });

  it('fires synchronously for maps without isStyleLoaded (slim test doubles)', () => {
    const map = { once: vi.fn(), on: vi.fn(), off: vi.fn() };
    const onReady = vi.fn();
    whenStyleReady(map as any, onReady);

    expect(onReady).toHaveBeenCalledTimes(1);
    expect(map.once).not.toHaveBeenCalled();
  });

  it('defers until the load event when the style is not loaded', () => {
    const map = makeMockMaplibreMap({ styleLoaded: false });
    const onReady = vi.fn();
    whenStyleReady(map as any, onReady);

    expect(onReady).not.toHaveBeenCalled();

    map._fire('load');
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('defers through styledata when the style reports loaded mid-reload (post-setStyle)', () => {
    const map = makeMockMaplibreMap({ styleLoaded: false });
    const onReady = vi.fn();
    whenStyleReady(map as any, onReady);

    // styledata while still loading must NOT fire the callback.
    map._fire('styledata');
    expect(onReady).not.toHaveBeenCalled();

    // The style finishes reloading (no second 'load'): flip the flag and let
    // the next styledata settle the wait.
    (map.isStyleLoaded as any).mockReturnValue(true);
    map._fire('styledata');
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('fires exactly once when both load and styledata arrive', () => {
    const map = makeMockMaplibreMap({ styleLoaded: false });
    const onReady = vi.fn();
    whenStyleReady(map as any, onReady);

    (map.isStyleLoaded as any).mockReturnValue(true);
    map._fire('styledata');
    map._fire('load');

    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('cancel detaches listeners and suppresses the pending callback', () => {
    const map = makeMockMaplibreMap({ styleLoaded: false });
    const onReady = vi.fn();
    const cancel = whenStyleReady(map as any, onReady);

    cancel();
    map._fire('load');

    expect(onReady).not.toHaveBeenCalled();
    // Listeners were detached — a later real load cannot resurrect the callback.
    map._fire('load');
    expect(onReady).not.toHaveBeenCalled();
  });
});
