import { describe, it, expect } from "vitest";
import { createStore } from "zustand/vanilla";
import { createLayersSlice, MAX_ANNOTATIONS } from "./layersSlice";
import { createUiSlice, MAX_OPS_LOG } from "./uiSlice";

// FE-3 (design §7): bounded UI state — annotations cap 500, opsLog cap 200
// (findings E4: unbounded arrays). Tests assert the caps hold and that the
// NEWEST entries survive eviction.

type SliceState = ReturnType<typeof createLayersSlice> & ReturnType<typeof createUiSlice>;

// Slice creators are typed Partial<HudState>; the composed vanilla store always
// holds the FULL state at runtime. Expose a Required-typed getState so the cap
// tests can call addAnnotation/pushOpLog without optional-chaining noise
// (tsconfig.test.json strictNullChecks).
type ComposedStore = {
  getState: () => Required<SliceState>;
  setState: (partial: Partial<SliceState>) => void;
  subscribe: (listener: (state: SliceState, prev: SliceState) => void) => () => void;
  getInitialState: () => SliceState;
};

function makeStore(): ComposedStore {
  const store = createStore<SliceState>()((set, get, api) => ({
    ...createLayersSlice(set as any, get as any, api as any),
    ...createUiSlice(set as any, get as any, api as any),
  }));
  return {
    getState: () => store.getState() as unknown as Required<SliceState>,
    setState: store.setState,
    subscribe: store.subscribe,
    getInitialState: store.getInitialState,
  };
}

describe("store slice caps (FE-3)", () => {
  describe("annotations (layersSlice)", () => {
    it(`keeps at most ${MAX_ANNOTATIONS} annotations, dropping the OLDEST`, () => {
      const store = makeStore();
      for (let i = 0; i < MAX_ANNOTATIONS + 10; i++) {
        store.getState().addAnnotation({ id: i } as any);
      }
      const annotations = store.getState().annotations;
      expect(annotations).toHaveLength(MAX_ANNOTATIONS);
      // Newest kept (append order preserved)…
      expect(annotations[annotations.length - 1]).toEqual({ id: MAX_ANNOTATIONS + 9 });
      // …oldest (0..9) evicted.
      expect(annotations[0]).toEqual({ id: 10 });
    });

    it("does not trim below the cap", () => {
      const store = makeStore();
      for (let i = 0; i < 3; i++) store.getState().addAnnotation({ id: i } as any);
      expect(store.getState().annotations).toHaveLength(3);
    });

    it("clearAnnotations empties the list", () => {
      const store = makeStore();
      store.getState().addAnnotation({ id: 1 } as any);
      store.getState().clearAnnotations();
      expect(store.getState().annotations).toEqual([]);
    });
  });

  describe("opsLog (uiSlice)", () => {
    it(`keeps at most ${MAX_OPS_LOG} entries, dropping the OLDEST`, () => {
      const store = makeStore();
      for (let i = 0; i < MAX_OPS_LOG + 10; i++) {
        store.getState().pushOpLog({ id: `op-${i}` } as any);
      }
      const log = store.getState().opsLog;
      expect(log).toHaveLength(MAX_OPS_LOG);
      // Newest is prepended → index 0.
      expect(log[0]).toEqual({ id: `op-${MAX_OPS_LOG + 9}` });
      // Oldest (op-0 .. op-9) evicted.
      expect(log[log.length - 1]).toEqual({ id: `op-${10}` });
    });

    it("does not trim below the cap", () => {
      const store = makeStore();
      for (let i = 0; i < 5; i++) store.getState().pushOpLog({ id: `op-${i}` } as any);
      expect(store.getState().opsLog).toHaveLength(5);
    });
  });
});
