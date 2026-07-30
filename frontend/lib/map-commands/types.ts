import type { MapActionPayload } from '@/lib/types';

/**
 * Command catalogue types.
 *
 * The catalogue owns the vocabulary, validation, and dispatch for every map
 * command the AI can emit. Each command case in the old `switch (action.command)`
 * (map-action-handler.tsx) is extracted verbatim into a slice file; those bodies
 * read from `MapCommandContext` instead of closing over the component's scope.
 *
 * Per the refactor spec: `MapCommandContext` carries ONLY the component-scope
 * runtime values each handler needs. Module-level helpers (`devOnly`,
 * `TILE_PROVIDERS`, `API_BASE`, the map-kit `navigation`/`renderer`/`exporter`
 * modules, annotation helpers, `parseFilter`) are imported directly in the slice
 * files that use them — they are never passed through context.
 *
 * `params` is typed `MapActionPayload['params']` (not `Record<string, unknown>`)
 * so destructuring inside the extracted bodies behaves exactly like the old
 * inline `case` bodies reading `action.params`. This is a pure type alias on the
 * already-imported payload type — it adds no runtime coupling and keeps the
 * extracted code byte-for-byte faithful to the originals.
 */
export interface MapCommandContext {
  // The MapLibre map instance (component's `mapInstance.getMap()`).
  map: any;
  // Dequeue the current action. Provided for completeness; sync pop is driven by
  // the component's finally block. Async handlers (export_map) pop themselves
  // via `safePop` once their deferred work resolves.
  popAction: () => void;
  // Mark the current action's pop as deferred so async handlers (export_map)
  // can opt out of the synchronous finally-pop and pop themselves later.
  setDeferredPop: (v: boolean) => void;
  // Pop guarded against double-pop (component's poppedRef machinery).
  safePop: () => void;
  // Store access — handlers call `useHudStore.getState()` today; this hands them
  // the same getter the component would use.
  getHudState: () => any;
  // useMapAction().setSelectedBaseLayer — used only by BASE_LAYER_CHANGE.
  setSelectedBaseLayer: (idx: number) => void;
  // The action being dispatched (normalized lowercase command name).
  command: string;
  // The action params. Typed as MapActionPayload['params'] to mirror the old
  // `action.params` destructuring exactly; passed at runtime as `action.params || {}`.
  params: MapActionPayload['params'];
}

/**
 * Minimal schema validator for a command's params. Mirrors the shape of the old
 * `REQUIRED_PARAMS` table in map-action-renderer.tsx — returns true when the
 * params have the fields/types the handler requires, false to reject before
 * dispatch. Value-domain validation stays with MapLibre / the store reducers.
 */
export type CommandValidator = (p: Record<string, unknown>) => boolean;

/**
 * A single command's vocabulary entry: its validator + its handler body.
 * The handler body is the verbatim extraction of the old `case` body.
 */
export interface CommandEntry {
  requiredParams: CommandValidator;
  run: (ctx: MapCommandContext) => void;
}
