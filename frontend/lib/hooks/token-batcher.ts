/**
 * Coalesces high-frequency SSE token chunks into per-animation-frame flushes.
 *
 * Transport goal §21 / F-FE-1 / D-F8: the streaming consumer used to call
 * React setState once per token. A 200-token turn produced ~200 renders (and,
 * because `messages` lives at page level, ~200 full-app re-renders that
 * re-parsed every message's markdown). This class accumulates token chunks
 * and flushes at most once per animation frame, turning ~200 renders into
 * ~20–50 (one per frame the tokens span), without losing the "live typing"
 * feel or delaying terminal events.
 *
 * It owns no React state — pass it an `onFlush` callback that does the
 * setState. The content/reasoning accumulators hold the FULL streamed text so
 * far (snapshot semantics, matching the prior `rawContentRef` behavior); each
 * flush delivers the latest snapshot, and the caller applies it. Terminal
 * events (done/error/step_result) must call `flush()` so the final text lands
 * before the status change.
 *
 * Pure/testable: `schedule`/`cancel` are injected so tests can use a
 * deterministic fake rAF and assert the coalescing ratio.
 */
export interface TokenBatcherSchedulers {
  schedule: (cb: () => void) => number;
  cancel: (id: number) => void;
}

export interface FlushedTokens {
  /** Full non-reasoning content accumulated so far. */
  content: string;
  /** Full reasoning content accumulated so far. */
  reasoning: string;
}

export class TokenBatcher {
  private content = "";
  private reasoning = "";
  private dirty = false;
  private scheduledId: number | null = null;

  constructor(
    private readonly schedulers: TokenBatcherSchedulers,
    private readonly onFlush: (snapshot: FlushedTokens) => void,
  ) {}

  /** Accumulate one token chunk and schedule a flush if not already pending. */
  push(chunk: string, isReasoning: boolean): void {
    if (isReasoning) this.reasoning += chunk;
    else this.content += chunk;
    this.dirty = true;
    if (this.scheduledId === null) {
      this.scheduledId = this.schedulers.schedule(() => this.flush());
    }
  }

  /** Emit any pending snapshot immediately (cancelling the scheduled flush).
   * Safe to call when nothing is pending (no-op). Returns the snapshot or null. */
  flush(): FlushedTokens | null {
    if (this.scheduledId !== null) {
      this.schedulers.cancel(this.scheduledId);
      this.scheduledId = null;
    }
    if (!this.dirty) return null;
    this.dirty = false;
    const snapshot = { content: this.content, reasoning: this.reasoning };
    this.onFlush(snapshot);
    return snapshot;
  }

  /** Reset accumulators for a new turn. Also cancels any pending flush. */
  reset(): void {
    this.content = "";
    this.reasoning = "";
    this.dirty = false;
    if (this.scheduledId !== null) {
      this.schedulers.cancel(this.scheduledId);
      this.scheduledId = null;
    }
  }
}
