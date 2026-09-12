/**
 * Journey mode resolution (ADR-0146 dual-mode contract).
 *
 * mock: page.route fixtures answer every product API; no backend process.
 * real: a real backend answers; the only env-gated skip is documented per
 * journey (never silent — the nightly lane runs REQUIRE_BROWSER=1, where a
 * missing browser is a hard red, not a skip).
 */
export type JourneyMode = 'mock' | 'real';

export const MODE: JourneyMode = process.env.E2E_MODE === 'real' ? 'real' : 'mock';

/** Backend origin the real frontend talks to (NEXT_PUBLIC_API_URL default :8001). */
export const API_ORIGIN = process.env.E2E_API_URL ?? 'http://localhost:8001';

/** Skip helper for real-mode variants: one place carries the honest reason. */
export const REAL_ONLY_REASON =
  'real-mode journey: needs a running backend (nightly quality-e2e lane; mock twin covers PRs)';

/** Skip helper for mock-mode variants. */
export const MOCK_ONLY_REASON =
  'mock-mode journey: fixtures cannot represent this surface; runs only against the real stack';
