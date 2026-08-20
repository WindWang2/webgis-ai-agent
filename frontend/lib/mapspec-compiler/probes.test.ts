import { describe, it, expect } from "vitest";
import { hexToRgb, colorWithinTolerance, dominantColorInWindow } from "./probes";

function makePng(
  width: number,
  height: number,
  pixel: (x: number, y: number) => [number, number, number, number]
): { width: number; height: number; data: Uint8Array } {
  const data = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const off = (y * width + x) * 4;
      const [r, g, b, a] = pixel(x, y);
      data[off] = r;
      data[off + 1] = g;
      data[off + 2] = b;
      data[off + 3] = a;
    }
  }
  return { width, height, data };
}

describe("hexToRgb", () => {
  it("parses 6-digit hex", () => {
    expect(hexToRgb("#de2d26")).toEqual({ r: 0xde, g: 0x2d, b: 0x26 });
    expect(hexToRgb("#2CA25F")).toEqual({ r: 0x2c, g: 0xa2, b: 0x5f });
  });
  it("parses 3-digit shorthand", () => {
    expect(hexToRgb("#abc")).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc });
  });
  it("parses without leading #", () => {
    expect(hexToRgb("ff0000")).toEqual({ r: 255, g: 0, b: 0 });
  });
  it("throws on invalid hex", () => {
    expect(() => hexToRgb("#12")).toThrow();
    expect(() => hexToRgb("")).toThrow();
  });
});

describe("colorWithinTolerance", () => {
  it("passes when within ±16", () => {
    expect(colorWithinTolerance({ r: 100, g: 100, b: 100 }, "#646464", 16)).toBe(true); // exact
    expect(colorWithinTolerance({ r: 116, g: 84, b: 100 }, "#646464", 16)).toBe(true); // boundary
  });
  it("fails when outside ±16", () => {
    expect(colorWithinTolerance({ r: 117, g: 100, b: 100 }, "#646464", 16)).toBe(false); // +17 on r
    expect(colorWithinTolerance({ r: 83, g: 100, b: 100 }, "#646464", 16)).toBe(false); // -17 on r
  });
  it("all channels must pass", () => {
    expect(colorWithinTolerance({ r: 100, g: 100, b: 117 }, "#646464", 16)).toBe(false);
  });
  it("supports custom tolerance", () => {
    expect(colorWithinTolerance({ r: 110, g: 100, b: 100 }, "#646464", 5)).toBe(false);
    expect(colorWithinTolerance({ r: 104, g: 100, b: 100 }, "#646464", 5)).toBe(true);
  });
});

describe("dominantColorInWindow", () => {
  it("picks majority color in window", () => {
    // 5x5 png all red except center is blue — red wins by majority
    const png = makePng(5, 5, (x, y) =>
      x === 2 && y === 2 ? [0, 0, 255, 255] : [255, 0, 0, 255]
    );
    expect(dominantColorInWindow(png, 2, 2, 2)).toEqual({ r: 255, g: 0, b: 0 });
  });

  it("ties → closest to center wins", () => {
    // 3x3 window centered at (1,1) with 2 colors each appearing equally;
    // center pixel is blue, corners red — blue is at dist 0, red at dist 1.
    // Make 4 blue + 4 red + center blue = 5 blue vs 4 red → blue wins anyway.
    // So test tie: 3x3, center blue, all others alternating.
    // Easier: 3x3 where exactly 4 red, 4 blue, 1 green at center — green dist 0 vs tie red/blue.
    // Use 1 center unique but minority: make 4 red, 4 green, 1 blue center — blue count 1, others 4, blue loses.
    // True tie test: 3x3, 4 red (at edges mid), 4 blue (at corners), 1 red center → 5 red vs 4 blue.
    // Let's construct 4 red + 4 blue + 1 red-center → not tie.
    // Tie: need equal count. 5x5 not needed; use 3x3 with half=1 (9 pixels):
    // put 4 red and 4 blue and 1 green center → red 4 tie blue 4, but green 1 alone loses.
    // Better: 3x3, 4 red at dist1 edges, 4 blue at dist1 corners, but can't tie without center.
    // Use 5 pixels: 3x3 with half=1, pick 2 red at (0,1)(2,1) dist1, 2 blue at (1,0)(1,2) dist1, center green.
    // counts 2,2,1 — tie red/blue, both dist1, first inserted wins? But we claim tie→center distance.
    // Since both dist1 equal, first-inserted red wins — test that center wins when it's tied.
    // Make center red: 3 red (center + 2 edges) vs 2 blue → red wins.
    // True tie with center advantage: 5 pixel cross pattern half=1, center + 4 arms =5, but our window is 9.
    // Just do 3x3 where center color appears once, each other color also once → all tied at 1, center dist0 wins.
    const png = makePng(3, 3, (x, y) => {
      if (x === 1 && y === 1) return [0, 255, 0, 255]; // center green
      // give each other pixel unique color except repeat green nowhere else
      return [x * 80 + 10, y * 80 + 10, 50, 255];
    });
    // All 9 colors unique except we made 8 others each unique — each count 1, green dist 0 wins
    const result = dominantColorInWindow(png, 1, 1, 1);
    expect(result).toEqual({ r: 0, g: 255, b: 0 });
  });

  it("returns null if window completely outside canvas", () => {
    const png = makePng(4, 4, () => [255, 0, 0, 255]);
    expect(dominantColorInWindow(png, -10, -10, 2)).toBeNull();
  });

  it("returns null if all sampled pixels are transparent", () => {
    const png = makePng(5, 5, () => [0, 0, 0, 0]);
    expect(dominantColorInWindow(png, 2, 2, 2)).toBeNull();
  });

  it("handles window partially outside canvas (clips)", () => {
    // 3x3 canvas all blue, query at corner (0,0) with half=2 → clips to available pixels
    const png = makePng(3, 3, () => [0, 0, 255, 255]);
    expect(dominantColorInWindow(png, 0, 0, 2)).toEqual({ r: 0, g: 0, b: 255 });
  });

  it("skips transparent pixels in count", () => {
    // 3x3 with center transparent, edges red — red wins even though center empty
    const png = makePng(3, 3, (x, y) =>
      x === 1 && y === 1 ? [0, 0, 0, 0] : [255, 0, 0, 255]
    );
    expect(dominantColorInWindow(png, 1, 1, 1)).toEqual({ r: 255, g: 0, b: 0 });
  });
});
