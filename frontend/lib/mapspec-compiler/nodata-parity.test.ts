/**
 * H02 Wave 5b — nodata guard 双端语义一致（前端侧）。
 *
 * 历史分歧（thematic-paint.ts:21-26 自述）：live 有 withNoDataGuard，
 * headless compileStyleMethod / SVG 孪生不编码 → null 值 live 走无数据色、
 * 导出压进最低类。契约：StyleMethod.nodata 在场时，四个下降点同语义。
 */
import { describe, expect, it } from "vitest";

import { compileStyleMethod } from "@/lib/mapspec-compiler/compiler";
import { resolvePaintValue } from "@/lib/mapspec-compiler/mapspec-to-svg";

const stepWithNodata = {
  method: "step" as const,
  field: "density",
  default: "#fcae91",
  stops: [
    [10, "#fb6a4a"],
    [20, "#cb181d"],
  ] as Array<[number, string]>,
  nodata: "#eeeeee",
};

const interpolateWithNodata = {
  method: "interpolate" as const,
  field: "value",
  stops: [
    [0, "#eff3ff"],
    [100, "#08519c"],
  ] as Array<[number, string]>,
  nodata: "#cccccc",
};

describe("H02 nodata guard parity", () => {
  it("compileStyleMethod wraps step with case guard (live/headless MapLibre lowering)", () => {
    const expr = compileStyleMethod(stepWithNodata) as unknown[];
    expect(Array.isArray(expr)).toBe(true);
    expect(expr[0]).toBe("case");
    // guard 形状与 thematic-paint.withNoDataGuard 一致：
    // ["case", ["==", ["get", field], null], nodata, thematic]
    const guard = expr[1] as unknown[];
    expect(guard[0]).toBe("==");
    expect(JSON.stringify(guard[1])).toBe(JSON.stringify(["get", "density"]));
    expect(guard[2]).toBeNull();
    expect(expr[2]).toBe("#eeeeee");
    const inner = expr[3] as unknown[];
    expect(inner[0]).toBe("step");
  });

  it("compileStyleMethod wraps interpolate with case guard", () => {
    const expr = compileStyleMethod(interpolateWithNodata) as unknown[];
    expect(expr[0]).toBe("case");
    expect(expr[2]).toBe("#cccccc");
    expect((expr[3] as unknown[])[0]).toBe("interpolate");
  });

  it("compileStyleMethod stays byte-compatible without nodata", () => {
    const { nodata: _omit, ...legacy } = stepWithNodata;
    const expr = compileStyleMethod(legacy) as unknown[];
    expect(expr[0]).toBe("step");
  });

  it("SVG twin resolvePaintValue diverts null/missing to nodata color", () => {
    expect(resolvePaintValue(stepWithNodata, { density: null })).toBe("#eeeeee");
    expect(resolvePaintValue(stepWithNodata, {})).toBe("#eeeeee");
    expect(resolvePaintValue(stepWithNodata, { density: 15 })).toBe("#fb6a4a");
    const { nodata: _omit, ...legacy } = stepWithNodata;
    expect(resolvePaintValue(legacy, { density: null })).toBe("#fcae91");
  });

  it("SVG twin interpolate nodata semantics", () => {
    expect(resolvePaintValue(interpolateWithNodata, { value: null })).toBe("#cccccc");
    expect(resolvePaintValue(interpolateWithNodata, { value: 50 })).toBeTruthy();
  });
});
