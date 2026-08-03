/**
 * MapSpec Compiler Engine
 *
 * Deep module presenting a single entrypoint seam (`compileMapSpec`) to translate
 * cartographic MapSpec specifications into MapLibre-compatible style configurations,
 * legend specifications, and HTML templates.
 */

export * from "./types";
export * from "./compiler";
export * from "./html-template";
// ADR-0036: pure spec diff (no MapLibre dependency). Re-exported here so the
// compiler package owns both compile + reconcile of the MapSpec domain model.
export * from "./reconciler";

