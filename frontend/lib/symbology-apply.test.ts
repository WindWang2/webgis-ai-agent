import { describe, it, expect } from "vitest";
import { applySymbology, SymbologyPayload } from "./symbology-apply";

describe("applySymbology reducer", () => {
  it("single mode: produces LAYER_STYLE_UPDATE with flat paint style", () => {
    const payload: SymbologyPayload = {
      mode: "single",
      geometry: "Polygon",
      style: { color: "#3b82f6", fillOpacity: 0.7, strokeColor: "#1d4ed8", strokeWidth: 1.5 },
    };

    const result = applySymbology(payload, "layer-1");

    expect(result.command).toBe("LAYER_STYLE_UPDATE");
    expect(result.params.layer_id).toBe("layer-1");
    expect(result.params.style_applied?.color).toBe("#3b82f6");
    expect(result.params.style_applied?.fill).toBe("#3b82f6");
    expect(result.params.style_applied?.fillOpacity).toBe(0.7);
    expect(result.params.style_applied?.strokeColor).toBe("#1d4ed8");
    expect(result.params.style_applied?.strokeWidth).toBe(1.5);
    // no categorical fields leaked
    expect(result.params.field).toBeUndefined();
    expect(result.params.colorMap).toBeUndefined();
  });

  it("single mode: normalizes snake_case style keys (stroke_width → strokeWidth)", () => {
    const payload: SymbologyPayload = {
      mode: "single",
      geometry: "Polygon",
      style: { fill_color: "#ef4444", opacity: 0.5, stroke_width: 2 },
    };

    const result = applySymbology(payload, "layer-2");

    expect(result.params.style_applied?.color).toBeUndefined();
    expect(result.params.style_applied?.fillOpacity).toBe(0.5);
    expect(result.params.style_applied?.strokeWidth).toBe(2);
    // undefined keys dropped (no color provided)
    expect(result.params.style_applied?.color).toBeUndefined();
  });

  it("single mode: empty style falls back gracefully (no crash, empty style_applied)", () => {
    const payload: SymbologyPayload = {
      mode: "single",
      geometry: "Point",
      style: {},
    };

    const result = applySymbology(payload, "layer-3");

    expect(result.command).toBe("LAYER_STYLE_UPDATE");
    expect(result.params.layer_id).toBe("layer-3");
    expect(result.params.style_applied).toBeDefined();
  });

  it("categorical mode: carries field + colorMap + baseStyle (field injected at apply-time)", () => {
    const payload: SymbologyPayload = {
      mode: "categorical",
      geometry: "Polygon",
      colorMap: { residential: "#fca5a5", commercial: "#93c5fd" },
      baseStyle: { fillOpacity: 0.75, strokeWidth: 0.5 },
    };

    const result = applySymbology(payload, "layer-4", "landuse");

    expect(result.command).toBe("LAYER_STYLE_UPDATE");
    expect(result.params.layer_id).toBe("layer-4");
    expect(result.params.field).toBe("landuse");
    expect(result.params.colorMap).toEqual({ residential: "#fca5a5", commercial: "#93c5fd" });
    expect(result.params.baseStyle).toEqual({ fillOpacity: 0.75, strokeWidth: 0.5 });
    // no single-mode fields leaked
    expect(result.params.style_applied).toBeUndefined();
  });

  it("categorical mode: field defaults to empty string when not provided", () => {
    const payload: SymbologyPayload = {
      mode: "categorical",
      geometry: "Polygon",
      colorMap: { a: "#000" },
    };

    const result = applySymbology(payload, "layer-5");

    expect(result.params.field).toBe("");
  });
});
