import { describe, it, expect } from "vitest";
import { IncrementalThinkParser, parseThink } from "./incremental-think";

/**
 * D-F7 / F-FE-4: the incremental parser must produce byte-identical results
 * to the reference `parseThink` for every input and at every intermediate
 * point of a stream (the UI reads the result on every flush, not just at the
 * end), including markers split across token boundaries.
 */

/** Feed every prefix of `s` one char at a time, checking parity at each step. */
function expectProgressiveParity(s: string): void {
  const parser = new IncrementalThinkParser();
  for (let i = 1; i <= s.length; i++) {
    parser.append(s[i - 1]);
    expect(parser.getResult()).toEqual(parseThink(s.slice(0, i)));
  }
}

/** Deterministic LCG so the fuzz split is reproducible. */
function seededChunks(s: string, seed: number, parts: number): string[] {
  let rng = seed;
  const next = () => {
    rng = (rng * 1103515245 + 12345) % 2147483648;
    return rng / 2147483648;
  };
  const cuts: number[] = [];
  while (cuts.length < parts - 1) {
    const c = 1 + Math.floor(next() * (s.length - 1));
    if (!cuts.includes(c)) cuts.push(c);
  }
  const sorted = [...cuts].sort((a, b) => a - b);
  const out: string[] = [];
  let prev = 0;
  for (const c of sorted) {
    out.push(s.slice(prev, c));
    prev = c;
  }
  out.push(s.slice(prev));
  return out;
}

/** Feed a random chunking of `s` through the parser; must match parseThink(s). */
function expectChunkedParity(s: string, seeds: number[]): void {
  for (const seed of seeds) {
    const chunks = seededChunks(s, seed, 9);
    const parser = new IncrementalThinkParser();
    for (const c of chunks) parser.append(c);
    expect(parser.getResult()).toEqual(parseThink(s));
  }
}

describe("IncrementalThinkParser — behavior parity with parseThink", () => {
  it("no think markers: content passes through, thinking empty", () => {
    const s = "你好，我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察。";
    expectProgressiveParity(s);
  });

  it("complete block in one append", () => {
    const s = "answer prefix <think>deep reasoning</think> final answer";
    expectProgressiveParity(s);
  });

  it("open block (no close yet): thinking grows, content frozen at <think>", () => {
    const s = "prefix <think>still reasoning and more and more";
    expectProgressiveParity(s);
  });

  it("stray close before open: close ignored, block stays open", () => {
    const s = "a</think>b<think>c</think>d";
    expectProgressiveParity(s);
  });

  it("both markers split across append boundaries", () => {
    const s = "a<think>inner</think>tail";
    const parser = new IncrementalThinkParser();
    for (const c of ["a<th", "ink>inner</th", "ink>tail"]) parser.append(c);
    expect(parser.getResult()).toEqual(parseThink(s));
  });

  it("open marker split across boundary, close later", () => {
    const s = "x<think>y</think>z";
    const parser = new IncrementalThinkParser();
    for (const c of ["x<th", "ink>y", "</th", "ink>z"]) parser.append(c);
    expect(parser.getResult()).toEqual(parseThink(s));
  });

  it("close exactly at the boundary start of an append", () => {
    const s = "a<think>b</think>c";
    const parser = new IncrementalThinkParser();
    for (const c of ["a<think>b</think>", "c"]) parser.append(c);
    expect(parser.getResult()).toEqual(parseThink(s));
  });

  it("whitespace after close is trimmed (incl. whitespace-only appends)", () => {
    const s = "a<think>b</think>   \n\nfinal";
    const parser = new IncrementalThinkParser();
    for (const c of ["a<think>b</think>", "   ", "\n\n", "final"]) parser.append(c);
    expect(parser.getResult()).toEqual(parseThink(s));
  });

  it("multiple blocks: only the first is extracted, rest stays in content", () => {
    const s = "a<think>one</think>b<think>two</think>c";
    expectProgressiveParity(s);
  });

  it("nested <think> inside thinking", () => {
    const s = "a<think>x<think>y</think>z";
    expectProgressiveParity(s);
  });

  it("empty appends are harmless", () => {
    const parser = new IncrementalThinkParser();
    parser.append("");
    parser.append("<think>");
    parser.append("");
    parser.append("abc");
    expect(parser.getResult()).toEqual(parseThink("<think>abc"));
  });

  it("reset() clears state for a new turn", () => {
    const parser = new IncrementalThinkParser();
    parser.append("old <think>reasoning</think> old tail");
    parser.reset();
    parser.append("new <think>fresh</think> new tail");
    expect(parser.getResult()).toEqual(parseThink("new <think>fresh</think> new tail"));
    expect(parser.consumedLength).toBe("new <think>fresh</think> new tail".length);
  });

  it("fuzz: random chunkings match parseThink (markers straddle boundaries)", () => {
    const cases = [
      "a<think>b</think>c",
      "x</think>y<think>z</think>w",
      "<think>only open",
      "</think>only close",
      "<think></think>",
      "  <think>  spacy  </think>  ",
      "plain text with no markers at all, 你好世界",
      "a<th...b</think>c<think>d",
      "中文 <think>推理中</think> 中文回答",
    ];
    for (const s of cases) expectChunkedParity(s, [1, 7, 42, 99, 1234]);
  });

  it("consumedLength tracks appended chars for delta computation", () => {
    const parser = new IncrementalThinkParser();
    expect(parser.consumedLength).toBe(0);
    parser.append("ab");
    parser.append("cdef");
    expect(parser.consumedLength).toBe(6);
  });
});
