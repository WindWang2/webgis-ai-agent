/**
 * Incremental think-block parsing for streamed assistant text.
 *
 * Transport goal §22 / D-F7 / F-FE-4: the streaming consumer used to re-parse
 * the FULL accumulated content on every flush (`parseThink`: two `indexOf`
 * scans over the whole buffer + slices) — O(n²) over a turn. This module
 * tracks the first `<think>` / `</think>` positions incrementally: each
 * `append(delta)` scans only the new characters (plus a ≤7-char carry so a
 * marker split across a delta boundary is still found), and `getResult()`
 * derives the same `{thinking, content}` from the tracked positions.
 *
 * The raw content is accumulated as chunks (O(1) per delta) and joined only
 * at `getResult()` — the one O(n) step per flush, which the caller needs
 * anyway to deliver the full content snapshot to React.
 */

export interface ThinkParseResult {
  thinking: string;
  content: string;
}

const THINK_OPEN = '<think>';
const THINK_CLOSE = '</think>';
const OPEN_LEN = THINK_OPEN.length; // 7
const CLOSE_LEN = THINK_CLOSE.length; // 8

/**
 * Pure, non-incremental reference implementation — verbatim extraction of the
 * pre-D-F7 `use-sse-stream.ts` `parseThink`. Kept as the perf-regression
 * baseline and as the semantic reference for the incremental parser.
 */
export function parseThink(raw: string): ThinkParseResult {
  const start = raw.indexOf(THINK_OPEN);
  const end = raw.indexOf(THINK_CLOSE);
  if (start !== -1 && end !== -1 && end > start) {
    return {
      thinking: raw.slice(start + OPEN_LEN, end),
      content: raw.slice(0, start) + raw.slice(end + CLOSE_LEN).trimStart(),
    };
  }
  if (start !== -1) {
    return { thinking: raw.slice(start + OPEN_LEN), content: raw.slice(0, start) };
  }
  return { thinking: '', content: raw };
}

/**
 * Stateful incremental equivalent of `parseThink`. Feed it the streamed
 * content one delta per flush (the new characters since the last flush, e.g.
 * `snapshot.content.slice(parser.consumedLength)`); `getResult()` then
 * returns exactly what `parseThink(accumulated)` would. Only the new delta
 * (plus the carry) is scanned, so the total parse work over a turn is O(n)
 * instead of O(n²).
 */
export class IncrementalThinkParser {
  private chunks: string[] = [];
  private totalLen = 0;
  private start = -1;
  private end = -1;
  // Trailing up-to-(markerLen-1) chars of the scanned content that could
  // still be the start of an undetected marker; re-searched against the next
  // delta so markers split across a delta boundary are not missed.
  private openCarry = '';
  private closeCarry = '';
  // Characters actually examined by the marker searches (the delta plus any
  // carry re-scan). A deterministic work metric: O(n) over a turn, whereas a
  // full-buffer re-parse per append would make this O(n²).
  private scanned = 0;

  /** Characters consumed so far — used to compute the next delta. */
  get consumedLength(): number {
    return this.totalLen;
  }

  /** Characters examined by the marker searches so far — deterministic work metric. */
  get scannedChars(): number {
    return this.scanned;
  }

  reset(): void {
    this.chunks = [];
    this.totalLen = 0;
    this.start = -1;
    this.end = -1;
    this.openCarry = '';
    this.closeCarry = '';
    this.scanned = 0;
  }

  append(delta: string): void {
    const base = this.totalLen;
    if (this.start === -1) {
      const region = this.openCarry + delta;
      this.scanned += region.length;
      const idx = region.indexOf(THINK_OPEN);
      if (idx !== -1) {
        this.start = base - this.openCarry.length + idx;
      } else {
        this.openCarry = region.slice(-(OPEN_LEN - 1));
      }
    }
    if (this.end === -1) {
      const region = this.closeCarry + delta;
      this.scanned += region.length;
      const idx = region.indexOf(THINK_CLOSE);
      if (idx !== -1) {
        this.end = base - this.closeCarry.length + idx;
      } else {
        this.closeCarry = region.slice(-(CLOSE_LEN - 1));
      }
    }
    this.chunks.push(delta);
    this.totalLen += delta.length;
  }

  getResult(): ThinkParseResult {
    const raw = this.chunks.join('');
    const { start, end } = this;
    if (start !== -1 && end !== -1 && end > start) {
      return {
        thinking: raw.slice(start + OPEN_LEN, end),
        content: raw.slice(0, start) + raw.slice(end + CLOSE_LEN).trimStart(),
      };
    }
    if (start !== -1) {
      return { thinking: raw.slice(start + OPEN_LEN), content: raw.slice(0, start) };
    }
    return { thinking: '', content: raw };
  }
}
