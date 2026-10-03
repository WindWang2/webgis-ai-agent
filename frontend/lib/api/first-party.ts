import { API_BASE } from './config';

/**
 * First-party URL guard for browser-native fetches that must carry session
 * credentials (MapLibre tiles/images, authenticated downloads).
 *
 * `API_BASE` is empty in production (Dockerfiles build with
 * `NEXT_PUBLIC_API_URL=""` so the bundle uses same-origin relative URLs
 * behind the reverse proxy) and an absolute origin in dev
 * (`http://localhost:8001`).
 *
 * - `API_BASE === ''`: the URL must resolve (WHATWG parsing, exactly what the
 *   browser's fetch does) to the page's own origin. Never prefix-test the raw
 *   string: protocol-relative `//host/...`, `/\host/...` (backslash is a
 *   slash for special schemes) and `/<TAB>/host/...` (TAB/LF are stripped)
 *   all START with `/` but resolve to a foreign host, which would leak the
 *   Bearer JWT / owner_token (F-02).
 * - `API_BASE` configured: the request URL's ORIGIN must equal the base's
 *   origin exactly (never a prefix match — `http://api.example.com.evil.com`
 *   must not satisfy `http://api.example.com`), and its path must fall under
 *   the base's path prefix.
 */
export function isFirstPartyUrl(url: string): boolean {
  if (!url) return false;
  if (!API_BASE) {
    // Outside a browser (SSR/tests) use an unroutable sentinel origin: a
    // relative path resolves onto it, any foreign host does not.
    const pageOrigin =
      typeof window !== 'undefined' && window.location?.origin && window.location.origin !== 'null'
        ? window.location.origin
        : 'http://first-party.invalid';
    try {
      return new URL(url, pageOrigin).origin === pageOrigin;
    } catch {
      return false;
    }
  }
  try {
    const base = new URL(API_BASE, 'http://localhost');
    const target = new URL(url, base);
    return target.origin === base.origin && target.pathname.startsWith(base.pathname);
  } catch {
    return false;
  }
}

/**
 * Reduce a (possibly absolute) URL to its origin-relative path + query, the
 * shape `apiFetch`/`apiFetchBlob` expect (their `buildRequest` prepends
 * `API_BASE`). Relative URLs pass through unchanged.
 */
export function toApiPath(url: string): string {
  if (!url) return url;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) {
    try {
      const u = new URL(url);
      return u.pathname + u.search;
    } catch {
      /* fall through to the relative passthrough below */
    }
  }
  return url;
}
