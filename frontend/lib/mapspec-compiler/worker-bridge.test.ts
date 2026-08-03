import { describe, expect, it } from "vitest";
import { WorkerReconcilerBridge } from "./worker-bridge";
import { MapSpec } from "./types";

describe("WorkerReconcilerBridge", () => {
  it("should perform diffing via sync fallback when Worker is undefined", async () => {
    const bridge = new WorkerReconcilerBridge();

    const prev: MapSpec = {
      version: "1.0",
      view: { center: [0, 0], zoom: 2 },
      basemap: { providerId: "carto-positron" },
      sources: {},
      layers: [],
    };

    const next: MapSpec = {
      version: "1.0",
      view: { center: [116.4, 39.9], zoom: 10 },
      basemap: { providerId: "carto-dark" },
      sources: {},
      layers: [],
    };

    const patch = await bridge.diffSpecsAsync(prev, next);

    expect(patch.sources).toBeDefined();
    expect(patch.layers).toBeDefined();

    bridge.destroy();
  });
});
