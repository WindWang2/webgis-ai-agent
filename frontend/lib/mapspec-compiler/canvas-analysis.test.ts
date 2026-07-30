import { describe, it, expect } from "vitest";
import { analyseCanvas, isBlank } from "./canvas-analysis";

function makeRgba(
  width: number,
  height: number,
  pixel: (x: number, y: number) => [number, number, number, number]
): Uint8Array {
  const buf = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const off = (y * width + x) * 4;
      const [r, g, b, a] = pixel(x, y);
      buf[off] = r;
      buf[off + 1] = g;
      buf[off + 2] = b;
      buf[off + 3] = a;
    }
  }
  return buf;
}

describe("analyseCanvas", () => {
  it("flags a fully-transparent canvas as blank", () => {
    const rgba = makeRgba(4, 4, () => [0, 0, 0, 0]);
    const stats = analyseCanvas(rgba, 4, 4);
    expect(stats.transparentRatio).toBe(1);
    expect(isBlank(stats).blank).toBe(true);
    expect(isBlank(stats).reason).toMatch(/transparent/);
  });

  it("flags a near-monochrome canvas as blank", () => {
    // every opaque pixel the same colour
    const rgba = makeRgba(4, 4, () => [50, 100, 150, 255]);
    const stats = analyseCanvas(rgba, 4, 4);
    expect(stats.transparentRatio).toBe(0);
    expect(stats.dominantRatio).toBe(1);
    expect(isBlank(stats).blank).toBe(true);
    expect(isBlank(stats).reason).toMatch(/monochrome/);
  });

  it("passes a canvas with varied content", () => {
    // checkerboard of two very different colours
    const rgba = makeRgba(8, 8, (x, y) =>
      (x + y) % 2 === 0 ? [10, 20, 30, 255] : [240, 200, 100, 255]
    );
    const stats = analyseCanvas(rgba, 8, 8);
    expect(stats.transparentRatio).toBe(0);
    expect(stats.dominantRatio).toBeCloseTo(0.5, 1);
    expect(stats.luminanceStdDev).toBeGreaterThan(3);
    expect(isBlank(stats).blank).toBe(false);
  });

  it("ignores fully-transparent pixels in dominant/luminance stats", () => {
    // half transparent, half one solid colour
    const rgba = makeRgba(4, 4, (x) => (x < 2 ? [0, 0, 0, 0] : [100, 100, 100, 255]));
    const stats = analyseCanvas(rgba, 4, 4);
    expect(stats.transparentRatio).toBe(0.5);
    // dominant computed over opaque only → still 1.0
    expect(stats.dominantRatio).toBe(1);
  });

  it("throws on an undersized buffer", () => {
    expect(() => analyseCanvas(new Uint8Array(10), 4, 4)).toThrow(/too small/);
  });

  it("reports the dominant colour in quantised form", () => {
    const rgba = makeRgba(4, 4, () => [18, 34, 51, 255]); // quantises to 16|32|48
    const stats = analyseCanvas(rgba, 4, 4);
    expect(stats.dominantColor).toEqual({ r: 16, g: 32, b: 48 });
  });
});
