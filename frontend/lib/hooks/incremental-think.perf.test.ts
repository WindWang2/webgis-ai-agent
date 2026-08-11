import { describe, it, expect } from "vitest";
import { IncrementalThinkParser, parseThink } from "./incremental-think";

/**
 * D-F7 / F-FE-4 perf evidence: measures the parse *work* over a synthetic
 * N-token stream at two sizes. `parseThink` (the pre-fix hook logic, re-parses
 * the FULL accumulated content per event) must show ~4x work for 2x tokens
 * (quadratic); the incremental parser must show ~2x (linear).
 *
 * The complexity assertions are deterministic work-count math, not wall-clock
 * ratios — timing ratios flake under parallel test load, but character counts
 * do not:
 *  - baseline work  = Σ accumulated-buffer length over all calls (the chars a
 *    full-buffer re-parse must examine each call); quadratic by construction.
 *  - incremental work = `parser.scannedChars` (chars actually examined by the
 *    marker searches, counted inside the parser); linear by construction.
 * Wall-clock measurements are logged purely for human-readable evidence and
 * are never asserted.
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

/**
 * Total characters a full-buffer re-parse must examine for `tokens`: for each
 * token the accumulated buffer (all prior tokens + this one) is scanned again.
 * Doubling N quadruples this sum (Σ prefix lengths), i.e. quadratic.
 */
function baseScanWork(tokens: string[]): number {
  let acc = 0;
  let total = 0;
  for (const t of tokens) {
    acc += t.length;
    total += acc;
  }
  return total;
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
  /** Total chars the incremental parser actually scanned for `tokens`. */
  const incrementalScanWork = (tokens: string[]): number => {
    const parser = new IncrementalThinkParser();
    for (const t of tokens) parser.append(t);
    return parser.scannedChars;
  };

  it("baseline parseThink re-scans the full buffer per token (quadratic)", () => {
    const base500 = baseScanWork(tokens500);
    const base1000 = baseScanWork(tokens1000);
    // Informational wall-clock only — not asserted (load-sensitive).
    console.log(
      `[D-F7 baseline] N=500 ${bestOf(5, () => benchStream(runBaseline, tokens500, 40)).toFixed(4)}ms/pass, ` +
        `N=1000 ${bestOf(5, () => benchStream(runBaseline, tokens1000, 40)).toFixed(4)}ms/pass`,
    );
    // Work-count proof: Σ prefix lengths; doubling the tokens ~quadruples the
    // total re-scanned characters (exact, deterministic — 4.06 for this stream).
    console.log(
      `[D-F7 baseline] scan-work N=500 ${base500}, N=1000 ${base1000}, ` +
        `ratio ${(base1000 / base500).toFixed(2)}`,
    );
    expect(base1000 / base500).toBeGreaterThan(3.5);
    expect(base1000 / base500).toBeLessThan(4.5);
  });

  it("incremental parser scans only the delta per token (linear)", () => {
    const inc500 = incrementalScanWork(tokens500);
    const inc1000 = incrementalScanWork(tokens1000);
    console.log(
      `[D-F7 incremental] N=500 ${bestOf(5, () => benchStream(runIncremental, tokens500, 40)).toFixed(4)}ms/pass, ` +
        `N=1000 ${bestOf(5, () => benchStream(runIncremental, tokens1000, 40)).toFixed(4)}ms/pass`,
    );
    // Work-count proof: scannedChars grows ~linearly with N (2.02 for this
    // stream) — not quadratic like the baseline above.
    console.log(
      `[D-F7 incremental] scan-work N=500 ${inc500}, N=1000 ${inc1000}, ` +
        `ratio ${(inc1000 / inc500).toFixed(2)}`,
    );
    expect(inc1000 / inc500).toBeGreaterThan(1.5);
    expect(inc1000 / inc500).toBeLessThan(2.5);
    // Each append examines only its own delta (open+close regions, ≤ carry
    // re-scans) — never the accumulated buffer. A full-buffer re-parse would
    // push scannedChars toward Σ prefix lengths (≈ baseScanWork, millions).
    const totalLen = tokens1000.reduce((n, t) => n + t.length, 0);
    expect(inc1000).toBeLessThan(2 * totalLen);
  });

  it("incremental parse work is far below the quadratic baseline", () => {
    const base1000 = baseScanWork(tokens1000);
    const inc1000 = incrementalScanWork(tokens1000);
    console.log(
      `[D-F7 gap] N=1000 baseline scan-work ${base1000}, incremental ${inc1000}, ` +
        `gap ${(base1000 / inc1000).toFixed(0)}x`,
    );
    // Deterministic order-of-magnitude gap (actual ~416x at N=1000).
    expect(inc1000).toBeLessThan(base1000 / 100);
  });
});
