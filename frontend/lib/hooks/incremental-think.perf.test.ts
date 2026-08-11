import { describe, it, expect } from "vitest";
import { IncrementalThinkParser, parseThink } from "./incremental-think";

/**
 * D-F7 / F-FE-4 perf evidence: measures the parse work over a synthetic
 * N-token stream at two sizes. `parseThink` (the pre-fix hook logic, re-parses
 * the FULL accumulated content per event) must show ~4x time for 2x tokens
 * (quadratic); the incremental parser must show ~2x (linear).
 */

/** A realistic reasoning-heavy turn: the think markers split across tokens. */
function buildTokenStream(n: number): string[] {
  const tokens: string[] = [];
  tokens.push("<th", "ink>"); // '<think>' straddles a token boundary
  const half = Math.floor(n / 2) - 2;
  for (let i = 0; i < half; i++) tokens.push(`reasoning token ${i} — `);
  tokens.push("</th", "ink>"); // '</think>' straddles a token boundary
  for (let i = 0; i < n - tokens.length; i++) tokens.push(`answer token ${i} — `);
  return tokens;
}

function benchStream(
  run: (tokens: string[]) => void,
  tokens: string[],
  minMs: number,
): number {
  run(tokens); // warmup (JIT)
  const start = performance.now();
  let count = 0;
  while (performance.now() - start < minMs) {
    run(tokens);
    count++;
  }
  return (performance.now() - start) / count;
}

/** Best-of-N ms-per-pass (min) — stable against GC/JIT noise. */
function bestOf(samples: number, fn: () => number): number {
  let best = Infinity;
  for (let i = 0; i < samples; i++) best = Math.min(best, fn());
  return best;
}

describe("think-block parse scaling (D-F7)", () => {
  const tokens500 = buildTokenStream(500);
  const tokens1000 = buildTokenStream(1000);

  const runBaseline = (tokens: string[]) => {
    let acc = "";
    for (const t of tokens) {
      acc += t;
      parseThink(acc);
    }
  };
  const runIncremental = (tokens: string[]) => {
    const parser = new IncrementalThinkParser();
    for (const t of tokens) parser.append(t);
  };

  it("baseline parseThink re-scans the full buffer per token (quadratic)", () => {
    const base500 = bestOf(5, () => benchStream(runBaseline, tokens500, 40));
    const base1000 = bestOf(5, () => benchStream(runBaseline, tokens1000, 40));
    console.log(
      `[D-F7 baseline] N=500 ${base500.toFixed(4)}ms/pass, ` +
        `N=1000 ${base1000.toFixed(4)}ms/pass, ratio ${(base1000 / base500).toFixed(2)}`,
    );
    // Quadratic: doubling the tokens ~quadruples the total parse work.
    expect(base1000 / base500).toBeGreaterThan(3);
  });

  it("incremental parser scans only the delta per token (linear)", () => {
    const inc500 = bestOf(5, () => benchStream(runIncremental, tokens500, 40));
    const inc1000 = bestOf(5, () => benchStream(runIncremental, tokens1000, 40));
    console.log(
      `[D-F7 incremental] N=500 ${inc500.toFixed(4)}ms/pass, ` +
        `N=1000 ${inc1000.toFixed(4)}ms/pass, ratio ${(inc1000 / inc500).toFixed(2)}`,
    );
    // Linear: doubling the tokens ~doubles the total parse work.
    expect(inc1000 / inc500).toBeGreaterThan(1.3);
    expect(inc1000 / inc500).toBeLessThan(3);
  });

  it("incremental parse work is far below the quadratic baseline", () => {
    const base1000 = bestOf(5, () => benchStream(runBaseline, tokens1000, 40));
    const inc1000 = bestOf(5, () => benchStream(runIncremental, tokens1000, 40));
    // The old path re-scans ~500k chars per pass; the incremental path scans
    // ~one token. Even a 4x margin is orders of magnitude below the gap.
    expect(inc1000).toBeLessThan(base1000 / 4);
  });
});
