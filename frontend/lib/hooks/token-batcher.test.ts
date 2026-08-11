import { describe, it, expect } from "vitest";
import { TokenBatcher, type FlushedTokens } from "./token-batcher";

/** A deterministic fake rAF: queues callbacks, fired only when tick() is called. */
function fakeRaf() {
  const queue: (() => void)[] = [];
  let next = 1;
  return {
    schedule: (cb: () => void) => {
      const id = next++;
      queue[id] = cb;
      return id;
    },
    cancel: (id: number) => {
      delete queue[id];
    },
    tick: () => {
      // Fire one scheduled callback (the earliest pending), like a single frame.
      const keys = Object.keys(queue)
        .filter((k) => queue[+k])
        .sort((a, b) => +a - +b);
      if (keys.length === 0) return false;
      const cb = queue[+keys[0]];
      delete queue[+keys[0]];
      cb();
      return true;
    },
    pending: () =>
      Object.keys(queue).filter((k) => queue[+k]).length,
  };
}

describe("TokenBatcher", () => {
  it("coalesces many chunks pushed within one frame into a single flush", () => {
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));

    // Simulate 200 token chunks arriving synchronously (sub-frame).
    for (let i = 0; i < 200; i++) b.push(`tok${i} `, false);

    // No flush yet — it is scheduled for the next frame.
    expect(flushes).toHaveLength(0);
    expect(raf.pending()).toBe(1);

    raf.tick(); // one frame fires the single coalesced flush
    expect(flushes).toHaveLength(1);
    expect(flushes[0].content.split(" ").filter(Boolean)).toHaveLength(200);
    expect(raf.pending()).toBe(0);
  });

  it("reduces 200 token chunks across ~N frames instead of 200 renders", () => {
    // Model a turn where each frame carries ~6 tokens (200 tokens / ~33 frames).
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));
    let frames = 0;
    for (let i = 0; i < 200; i++) {
      b.push("x", false);
      if (i % 6 === 5) {
        raf.tick();
        frames++;
      }
    }
    b.flush();
    // ~33 frame flushes + maybe a trailing flush — far below 200.
    expect(flushes.length).toBeLessThan(50);
    expect(flushes.length).toBe(frames + (flushes.length > frames ? 1 : 0));
  });

  it("delivers a full snapshot each flush (content accumulates)", () => {
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));
    b.push("Hello ", false);
    raf.tick();
    b.push("World", false);
    raf.tick();
    expect(flushes[0].content).toBe("Hello ");
    expect(flushes[1].content).toBe("Hello World");
  });

  it("accumulates reasoning separately from content", () => {
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));
    b.push("think", true);
    b.push("answer", false);
    raf.tick();
    expect(flushes[0]).toEqual({ content: "answer", reasoning: "think" });
  });

  it("flush() emits pending immediately and cancels the scheduled rAF", () => {
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));
    b.push("a", false);
    expect(raf.pending()).toBe(1);
    const out = b.flush();
    expect(raf.pending()).toBe(0);
    expect(out).toEqual({ content: "a", reasoning: "" });
    expect(flushes).toHaveLength(1);
    // A terminal event calls flush() then nothing re-fires.
    raf.tick();
    expect(flushes).toHaveLength(1);
  });

  it("flush() with nothing pending is a no-op", () => {
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));
    expect(b.flush()).toBeNull();
    expect(flushes).toHaveLength(0);
  });

  it("reset() clears accumulators and cancels pending flush", () => {
    const raf = fakeRaf();
    const flushes: FlushedTokens[] = [];
    const b = new TokenBatcher(raf, (s) => flushes.push(s));
    b.push("a", false);
    b.reset();
    expect(raf.pending()).toBe(0);
    b.push("b", false);
    raf.tick();
    expect(flushes[0].content).toBe("b");
  });
});
