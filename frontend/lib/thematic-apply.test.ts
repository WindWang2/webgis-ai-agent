import { describe, it, expect } from "vitest";
import { resolveThematicPreset, ThematicPresetPayload } from "./thematic-apply";

describe("resolveThematicPreset reducer", () => {
  it("choropleth variant (natural_breaks) returns create_thematic_map tool call & graduated legend spec", () => {
    const payload: ThematicPresetPayload = {
      variant: "choropleth",
      method: "natural_breaks",
      k: 5,
      palette: "Viridis",
    };

    const res = resolveThematicPreset(payload, "gdp");

    expect(res.variant).toBe("choropleth");
    expect(res.toolCall.tool).toBe("create_thematic_map");
    expect(res.toolCall.params.field).toBe("gdp");
    expect(res.toolCall.params.method).toBe("natural_breaks");
    expect(res.toolCall.params.k).toBe(5);
    expect(res.toolCall.params.palette).toBe("Viridis");
    expect(res.legendSpec.type).toBe("graduated");
    expect(res.legendSpec.field).toBe("gdp");
  });

  it("choropleth variant (lisa) returns categorical legend spec", () => {
    const payload: ThematicPresetPayload = {
      variant: "choropleth",
      method: "lisa",
      k: 4,
      palette: "RdBu",
    };

    const res = resolveThematicPreset(payload, "lisa_cluster");

    expect(res.variant).toBe("choropleth");
    expect(res.toolCall.tool).toBe("create_thematic_map");
    expect(res.legendSpec.type).toBe("categorical");
  });

  it("heatmap variant returns add_native_heatmap tool call & continuous legend spec", () => {
    const payload: ThematicPresetPayload = {
      variant: "heatmap",
      intensity: 0.85,
      radius: 30,
      heatPalette: ["#0000ff", "#00ffff", "#00ff00", "#ffff00", "#ff0000"],
    };

    const res = resolveThematicPreset(payload, "density");

    expect(res.variant).toBe("heatmap");
    expect(res.toolCall.tool).toBe("add_native_heatmap");
    expect(res.toolCall.params.field).toBe("density");
    expect(res.toolCall.params.radius).toBe(30);
    expect(res.legendSpec.type).toBe("continuous");
    expect(res.legendSpec.field).toBe("density");
  });
});
