import { describe, it, expect } from "vitest";
import { compileMapSpec } from "./compiler";
import { MapSpec } from "./types";

/**
 * ADR-0092 Phase D — OD flow (flow_od_arc) MapSpec compilation.
 *
 * The flow vertical slice renders OD flows as MapLibre line layers with
 * data-driven paint: line-width and line-color interpolate on the feature's
 * `weight` property. These tests lock the compile seam so a flow layer can
 * never silently degrade to a constant-width line.
 */

function flowSpec(): MapSpec {
  return {
    version: "1.0.0",
    sources: {
      flows: {
        type: "geojson",
        inlineData: {
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              id: "o1->d1",
              geometry: {
                type: "LineString",
                coordinates: [
                  [104.06, 30.57],
                  [104.12, 30.66],
                ],
              },
              properties: { id: "o1->d1", origin_id: "o1", destination_id: "d1", weight: 42 },
            },
          ],
        },
      },
    },
    layers: [
      {
        id: "flow-layer",
        source: "flows",
        type: "line",
        visible: true,
        paint: {
          width: {
            method: "interpolate",
            field: "weight",
            stops: [
              [1, 1],
              [120, 8],
            ],
          },
          color: {
            method: "interpolate",
            field: "weight",
            stops: [
              [1, "#f2495c"],
              [120, "#7c3aed"],
            ],
          },
          opacity: 0.85,
        },
      } as any,
    ],
    layout: { components: [] },
  } as unknown as MapSpec;
}

describe("flow_od_arc layer compilation (ADR-0092 Phase D)", () => {
  it("compiles weight-driven line-width into a maplibre interpolate expression", () => {
    const result = compileMapSpec(flowSpec()) as any;
    expect(result.report.success).toBe(true);
    const style = result.style;
    const layer = style.layers.find((l: any) => l.id === "flow-layer");
    expect(layer).toBeDefined();
    expect(layer.type).toBe("line");
    expect(layer.paint["line-width"]).toEqual([
      "interpolate",
      ["linear"],
      ["to-number", ["get", "weight"]],
      1,
      1,
      120,
      8,
    ]);
  });

  it("compiles weight-driven line-color and opacity", () => {
    const result = compileMapSpec(flowSpec()) as any;
    expect(result.report.success).toBe(true);
    const style = result.style;
    const layer = style.layers.find((l: any) => l.id === "flow-layer");
    expect(layer.paint["line-color"][2]).toEqual(["to-number", ["get", "weight"]]);
    expect(layer.paint["line-opacity"]).toBe(0.85);
  });

  it("preserves stable per-flow feature ids through compilation", () => {
    const result = compileMapSpec(flowSpec()) as any;
    const style = result.style;
    // SelectionContext binds map features to table rows by feature id — the
    // compiled source data must retain the `${origin_id}->${destination_id}`
    // identity (a compiler that dropped/re-keyed ids would silently break
    // flow selection).
    const src = style.sources.flows;
    const data = src?.data ?? src?.inlineData;
    const feature = (data as any)?.features?.[0];
    expect(feature).toBeDefined();
    expect(feature.id).toBe("o1->d1");
    expect(feature.properties.id).toBe("o1->d1");
    expect(feature.properties.origin_id).toBe("o1");
    expect(feature.properties.destination_id).toBe("d1");
  });
});
