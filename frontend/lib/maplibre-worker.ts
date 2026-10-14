import { setWorkerUrl } from "maplibre-gl";

/**
 * #1597 — Point maplibre-gl at a served copy of its Web Worker.
 *
 * In the Turbopack dev environment maplibre-gl's default worker URL
 * (`import.meta.url`-derived) resolves to a non-`http(s):` value, so the worker
 * that fetches vector (MVT) tiles is constructed with an empty URL and never
 * loads. We set it to the same-origin route that serves the worker + shared
 * module from node_modules (see app/api/maplibre-worker/[file]/route.ts).
 *
 * Must run before any Map is created. Import for side effect from the module
 * that creates the map.
 */
setWorkerUrl("/api/maplibre-worker/maplibre-gl-worker.mjs");
