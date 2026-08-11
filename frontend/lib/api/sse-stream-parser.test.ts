import { describe, it, expect } from "vitest";
import { parseSSEStream, type ParseSSEOptions } from "./sse-stream-parser";

const enc = new TextEncoder();

/** Build a ReadableStream that emits the given chunks then closes. */
function streamFromChunks(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoded = chunks.map((c) => (typeof c === "string" ? enc.encode(c) : c));
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of encoded) controller.enqueue(c);
      controller.close();
    },
  });
}

async function collect(
  chunks: (string | Uint8Array)[],
  opts?: ParseSSEOptions,
  signal?: AbortSignal,
) {
  const out: { event: string; data: unknown; id?: string }[] = [];
  for await (const ev of parseSSEStream(streamFromChunks(chunks), signal, opts)) {
    out.push({
      event: ev.event,
      data: ev.data,
      ...(ev.id !== undefined ? { id: ev.id } : {}),
    });
  }
  return out;
}

describe("parseSSEStream", () => {
  it("parses multiple events delivered in one chunk", async () => {
    const evs = await collect([
      "event: token\ndata: {\"content\":\"a\"}\n\nevent: done\ndata: {}\n\n",
    ]);
    expect(evs).toEqual([
      { event: "token", data: { content: "a" } },
      { event: "done", data: {} },
    ]);
  });

  it("reassembles an event split across chunks (mid-line, mid-field)", async () => {
    const evs = await collect([
      "event: tok",
      "en\ndata: {\"cont",
      'ent":"b"}\n\n',
    ]);
    expect(evs).toEqual([{ event: "token", data: { content: "b" } }]);
  });

  it("handles CRLF split across a chunk boundary (B-P2-10)", async () => {
    // The event terminator (blank line) is a CRLF split across the boundary:
    // chunk1 ends with the first event + the \r of the blank line; chunk2
    // begins with the \n that completes it, then the second event. A parser
    // that splits on the full /\r?\n/ can drop the line; we split on \n and
    // strip a trailing \r per line so both events dispatch correctly.
    const evs = await collect([
      "event: token\ndata: {\"x\":1}\r",
      "\n\r\nevent: done\ndata: {}\n\n",
    ]);
    expect(evs).toHaveLength(2);
    expect(evs[0]).toEqual({ event: "token", data: { x: 1 } });
    expect(evs[1]).toEqual({ event: "done", data: {} });
  });

  it("handles plain CRLF and LF endings equivalently", async () => {
    const crlf = await collect(["event: a\ndata: 1\r\n\r\n"]);
    const lf = await collect(["event: a\ndata: 1\n\n"]);
    expect(crlf).toEqual(lf);
  });

  it("joins multi-line data: fields with \\n per spec", async () => {
    const evs = await collect(["event: msg\ndata: line1\ndata: line2\n\n"]);
    expect(evs[0].data).toBe("line1\nline2");
  });

  it("flushes a final event that has no trailing blank line (B-P2-9)", async () => {
    const evs = await collect(["event: tail\ndata: {\"z\":9}"]); // no \n\n
    expect(evs).toEqual([{ event: "tail", data: { z: 9 } }]);
  });

  it("flushes a trailing partial UTF-8 char at EOF (B-P2-11)", async () => {
    // "文" is 3 bytes in UTF-8; split it so the last byte arrives in the final
    // chunk with no following data. The decoder must be flushed at EOF.
    const full = enc.encode("event: t\ndata: \"文\"\n\n");
    const mid = full.indexOf(0xe6); // first byte of 文
    const a = full.slice(0, mid + 1);
    const b = full.slice(mid + 1);
    const evs = await collect([a, b]);
    expect(evs).toEqual([{ event: "t", data: "文" }]);
  });

  it("treats the [DONE] sentinel as a terminator and does not yield it", async () => {
    const evs = await collect(
      ['event: token\ndata: {"content":"hi"}\n\ndata: [DONE]\n\n'],
      { doneSentinel: "[DONE]" },
    );
    expect(evs).toEqual([{ event: "token", data: { content: "hi" } }]);
  });

  it("yields malformed-JSON data as a raw string", async () => {
    const evs = await collect(["event: err\ndata: not-json\n\n"]);
    expect(evs[0].data).toBe("not-json");
  });

  it("ignores comment (keepalive) lines without resetting the event", async () => {
    const evs = await collect(["event: token\ndata: {\"c\":1}\n: keepalive\n\n"]);
    expect(evs).toEqual([{ event: "token", data: { c: 1 } }]);
  });

  it("parses data: and event: with no leading space (A-F-11)", async () => {
    const evs = await collect(["event:token\ndata:{\"k\":1}\n\n"]);
    expect(evs).toEqual([{ event: "token", data: { k: 1 } }]);
  });

  it("dispatches an event that has no data line with empty-string data", async () => {
    const evs = await collect(["event: ping\n\n"]);
    expect(evs).toEqual([{ event: "ping", data: "" }]);
  });

  it("stops immediately when the AbortSignal is already set", async () => {
    const controller = new AbortController();
    controller.abort();
    const stream = streamFromChunks([
      "event: a\ndata: {}\n\n",
      "event: b\ndata: {}\n\n",
    ]);
    const out: unknown[] = [];
    for await (const ev of parseSSEStream(stream, controller.signal)) {
      out.push(ev);
    }
    expect(out).toHaveLength(0);
  });

  it("strips only a single leading space from data values", async () => {
    // "data:  x" -> " x" (only one leading space stripped), not "x"
    const evs = await collect(["event: e\ndata:  x\n\n"]);
    expect(evs[0].data).toBe(" x");
  });

  // ─── DUP-1: id: parsing for Last-Event-ID resume ─────────────────────────

  it("exposes the id: field on the dispatched event (DUP-1)", async () => {
    const evs = await collect([
      "event: token\nid: 7\ndata: {\"content\":\"a\"}\n\nevent: done\nid: 8\ndata: {}\n\n",
    ]);
    expect(evs).toEqual([
      { event: "token", data: { content: "a" }, id: "7" },
      { event: "done", data: {}, id: "8" },
    ]);
  });

  it("takes the LAST id: line when multiple are present (spec)", async () => {
    const evs = await collect(["event: token\nid: 3\nid: 9\ndata: {\"c\":1}\n\n"]);
    expect(evs[0].id).toBe("9");
  });

  it("omits id when the event has no id: line", async () => {
    const evs = await collect(["event: done\ndata: {}\n\n"]);
    expect(evs[0]).toEqual({ event: "done", data: {} });
    expect(evs[0].id).toBeUndefined();
  });

  it("reassembles an id: line split across chunks", async () => {
    const evs = await collect([
      "event: token\nid: 1",
      "2\ndata: {\"c\":1}\n\n",
    ]);
    expect(evs[0].id).toBe("12");
  });

  it("does not reset the event when an id: line appears (id is a field)", async () => {
    // The id line belongs to the in-flight event; a comment or id line must not
    // dispatch or reset it.
    const evs = await collect([
      "event: token\nid: 5\ndata: {\"c\":1}\n: keepalive\n\n",
    ]);
    expect(evs).toEqual([{ event: "token", data: { c: 1 }, id: "5" }]);
  });
});
