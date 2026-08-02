// ADR-0036: MapSpecRuntime deep module — reconciles a declarative MapSpec
// against a live MapLibre map instance via minimal diff/patch.
export { MapSpecRuntime } from "./runtime";
export { hudStateToMapSpec, SUBLAYER_SEP } from "./adapter";
export type { HudToSpecInput } from "./adapter";
