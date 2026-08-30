// ADR-0036: MapSpecRuntime deep module — reconciles a declarative MapSpec
// against a live MapLibre map instance via minimal diff/patch.
export { MapSpecRuntime } from "./runtime";
export { hudStateToMapSpec, SUBLAYER_SEP } from "./adapter";
export { collectCartographicRuntimeObservation } from "./runtime-evidence";
export type { HudToSpecInput } from "./adapter";
// P9: render observation — bounded evidence of the actual browser render.
export {
  collectRenderObservation,
  observeComponents,
  RuntimeErrorRing,
  waitForRenderSettle,
  RENDER_SETTLE_TIMEOUT_MS,
  MAX_RUNTIME_ERRORS,
  MAX_OBSERVED_COMPONENTS,
} from "./render-observation";
export type {
  RenderObservation,
  ObservedComponent,
  ObservedRuntimeError,
} from "./render-observation";
