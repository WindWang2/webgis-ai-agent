/**
 * Robust, transport-grade SSE (Server-Sent Events) stream parser.
 *
 * Extracted from the inline parsers in `chat.ts` and `explorer.ts` (which had
 * diverged — the explorer copy dropped the final unflushed event and leaked the
 * connection on abort) so there is one correct, tested implementation.
 *
 * Spec: https://html.spec.whatwg.org/multipage/server-sent-events.html
 *
 * Handles (transport goal §9 / audit B-P2-10/11, A-F-11):
 *   - chunks split anywhere (mid-event, mid-line, mid-field, mid-UTF-8)
 *   - CRLF, LF, and bare CR line endings, INCLUDING a CRLF split across two
 *     chunks (the prior `split(/\r?\n/)` silently dropped a `data:` line when
 *     `\r` and `\n` landed in different reads — we split on `\n` and strip a
 *     trailing `\r` per line instead)
 *   - multi-line `data:` fields joined with `\n` per spec
 *   - partial UTF-8 sequences across chunks via `TextDecoder({stream:true})`,
 *     AND a final `decode()` flush at EOF so a trailing multi-byte char is not
 *     lost (the prior parsers never flushed the decoder)
 *   - a final event without a trailing blank line (flushed at EOF)
 *   - the `[DONE]` sentinel (OpenAI-style) — opt-in via `doneSentinel`
 *   - malformed JSON in `data:` → yielded as a raw string (consumer decides)
 *   - comment lines (`:`) ignored — never dispatched, never reset the event
 *   - `data:` / `event:` with OR without the optional single leading space
 *   - an event with no `data:` line is still dispatched (data === "")
 *   - unknown fields (`id:`, `retry:`, ...) are ignored per spec
 *   - AbortSignal: cancels the reader and stops promptly (interrupts a pending
 *     read, and swallows the read() rejection that a real fetch abort produces,
 *     so cancellation is clean rather than thrown into the consumer)
 */
export interface SSEStreamEvent {
  /** Event type from the `event:` field (SSE default is "message"). */
  event: string;
  /** Parsed JSON value, or the raw string if it was not valid JSON / empty. */
  data: Record<string, unknown> | unknown[] | string;
}

export interface ParseSSEOptions {
  /**
   * If set (e.g. `"[DONE]"`), a `data:` payload equal to this sentinel is
   * treated as a stream terminator and NOT yielded. The OpenAI streaming
   * contract uses `[DONE]`; the WebGIS chat backend emits it.
   */
  doneSentinel?: string;
}

/**
 * Parse a `ReadableStream<Uint8Array>` (an SSE response body) into a stream of
 * events. Pure: no `fetch`, no global state — pass it the body and an optional
 * AbortSignal. Drives backpressure naturally (yields one event at a time).
 */
export async function* parseSSEStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  options: ParseSSEOptions = {},
): AsyncGenerator<SSEStreamEvent> {
  const { doneSentinel } = options;
  const reader = body.getReader();
  const decoder = new TextDecoder();

  let buffer = "";
  let currentEvent = "";
  let currentData = "";
  let haveData = false; // a `data:` line was seen (distinct from empty data)

  /** Process one physical line, pushing any dispatched event into `out`.
   * Returns true if the done-sentinel was seen (stream should terminate). */
  const processLine = (line: string, out: SSEStreamEvent[]): boolean => {
    if (line === "") {
      // Blank line = dispatch the current event (if any).
      if (!currentEvent && !haveData) return false;
      const event = currentEvent || "message";
      let data: SSEStreamEvent["data"];
      if (haveData) {
        if (doneSentinel && currentData.trim() === doneSentinel) {
          currentEvent = "";
          currentData = "";
          haveData = false;
          return true; // terminator — do not yield
        }
        try {
          data = JSON.parse(currentData) as Record<string, unknown> | unknown[];
        } catch {
          data = currentData; // malformed JSON → raw string
        }
      } else {
        data = ""; // event with no data line
      }
      currentEvent = "";
      currentData = "";
      haveData = false;
      out.push({ event, data });
      return false;
    }
    if (line.charCodeAt(0) === 58) return false; // ':' comment line — ignore
    const colon = line.indexOf(":");
    if (colon === -1) return false; // field with no colon — ignore (unknown)
    const field = line.slice(0, colon);
    // Per spec: strip a single leading space from the value if present.
    let valuePart = line.slice(colon + 1);
    if (valuePart.charCodeAt(0) === 32) valuePart = valuePart.slice(1);
    if (field === "event") {
      currentEvent = valuePart;
    } else if (field === "data") {
      if (haveData) currentData += "\n";
      currentData += valuePart;
      haveData = true;
    }
    // id: / retry: / unknown → ignored
    return false;
  };

  /** Split a chunk into complete lines, processing each; carry the trailing
   * partial line back into `buffer`. Returns the events to yield and whether
   * the done-sentinel terminated the stream. */
  const processChunk = (text: string): { events: SSEStreamEvent[]; done: boolean } => {
    buffer += text;
    const parts = buffer.split("\n");
    buffer = parts.pop() ?? ""; // incomplete trailing line carried to next read
    const events: SSEStreamEvent[] = [];
    for (const rawLine of parts) {
      const line =
        rawLine.length > 0 && rawLine.charCodeAt(rawLine.length - 1) === 13
          ? rawLine.slice(0, -1) // strip trailing \r (handles \r\n + cross-chunk)
          : rawLine;
      if (processLine(line, events)) return { events, done: true };
    }
    return { events, done: false };
  };

  // Abort must interrupt a PENDING reader.read() (a loop-top check alone misses
  // an abort that fires while blocked in read()). Cancelling the reader
  // resolves the pending read; for a real fetch stream the body is also errored
  // by the abort and read() rejects — caught below as a clean stop.
  const onAbort = () => {
    reader.cancel().catch(() => {
      /* already closed */
    });
  };
  if (signal) {
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  }

  try {
    while (true) {
      if (signal?.aborted) break;
      let done = true;
      let value: Uint8Array | undefined;
      try {
        ({ done, value } = await reader.read());
      } catch {
        if (signal?.aborted) break; // read() rejected due to fetch abort
        throw new Error("SSE stream read failed");
      }
      if (done) {
        // Flush any trailing partial UTF-8 (prior parsers lost the last char of
        // a split multi-byte sequence at EOF), then process the final line that
        // had no trailing newline, then dispatch the in-flight event.
        const tail = decoder.decode();
        if (tail) buffer += tail;
        const finalEvents: SSEStreamEvent[] = [];
        if (buffer) {
          if (processLine(buffer, finalEvents)) {
            for (const ev of finalEvents) yield ev;
            return;
          }
          buffer = "";
        }
        processLine("", finalEvents); // blank line dispatches the pending event
        for (const ev of finalEvents) yield ev;
        break;
      }
      const { events, done: streamDone } = processChunk(decoder.decode(value!, { stream: true }));
      for (const ev of events) yield ev;
      if (streamDone) return;
    }
  } finally {
    if (signal) signal.removeEventListener("abort", onAbort);
    try {
      await reader.cancel();
    } catch {
      /* already closed */
    }
    // releaseLock is optional-chained: test doubles / some reader wrappers may
    // not implement it, and after cancel() a real reader is already unlocked.
    (reader as { releaseLock?: () => void }).releaseLock?.();
  }
}
