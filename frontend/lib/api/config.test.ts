import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * E-F-2: API base must use ?? (not ||) so a production build that sets
 * NEXT_PUBLIC_API_URL="" (same-origin, injected by the Dockerfiles) wins over
 * the dev fallback. With ||, "" is falsy and the bundle would silently fall
 * back to http://localhost:8001 — every API call in prod would hit the user's
 * own machine. These tests pin the ?? semantics.
 */
const KEY = "NEXT_PUBLIC_API_URL";
const WS_KEY = "NEXT_PUBLIC_WS_URL";

describe("API config (E-F-2: ?? not ||)", () => {
  beforeEach(() => {
    vi.resetModules();
    delete process.env[KEY];
    delete process.env[WS_KEY];
  });

  it("empty string -> same-origin (empty), NOT the localhost fallback", async () => {
    process.env[KEY] = "";
    const { API_BASE } = await import("./config");
    expect(API_BASE).toBe("");
  });

  it("unset -> dev fallback http://localhost:8001", async () => {
    delete process.env[KEY];
    const { API_BASE } = await import("./config");
    expect(API_BASE).toBe("http://localhost:8001");
  });

  it("explicit value is honored", async () => {
    process.env[KEY] = "https://api.example.com";
    const { API_BASE } = await import("./config");
    expect(API_BASE).toBe("https://api.example.com");
  });

  it("WS base mirrors the same ?? semantics", async () => {
    process.env[WS_KEY] = "";
    const { WS_BASE } = await import("./config");
    expect(WS_BASE).toBe("");
  });

  it("F-04: unset WS var follows API base (dev) instead of a hardcoded port", async () => {
    process.env[KEY] = "https://api.example.com";
    const { WS_BASE, getWsBase } = await import("./config");
    expect(WS_BASE).toBe("wss://api.example.com");
    expect(getWsBase()).toBe("wss://api.example.com");
  });

  it("F-04: same-origin build derives ws(s)://<page host> at runtime, never localhost", async () => {
    process.env[KEY] = "";
    const { getWsBase } = await import("./config");
    // jsdom default location is http://localhost:3000 (vitest) — host-derived.
    expect(getWsBase()).toBe(`ws://${window.location.host}`);
    expect(getWsBase()).not.toContain("8001");
  });

  it("F-04: https pages get wss://", async () => {
    process.env[KEY] = "";
    process.env[WS_KEY] = "";
    const { getWsBase } = await import("./config");
    const orig = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { protocol: "https:", host: "gis.example.org" },
    });
    try {
      expect(getWsBase()).toBe("wss://gis.example.org");
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: orig });
    }
  });

  it("explicit WS var still wins", async () => {
    process.env[KEY] = "";
    process.env[WS_KEY] = "wss://ws.example.org";
    const { getWsBase } = await import("./config");
    expect(getWsBase()).toBe("wss://ws.example.org");
  });
});
