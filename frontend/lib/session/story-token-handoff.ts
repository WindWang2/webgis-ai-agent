/**
 * F-09: hand the anonymous-session owner_token to the Story tab.
 *
 * `/story?session_id=…` opens in a NEW tab, but owner tokens live only in the
 * opener tab's memory (SEC-08), so an anonymous session's Story always
 * 401/404'd. The raw token must never go into the URL (history / Referer
 * leak). Instead the Story tab asks its opener over a same-origin
 * postMessage handshake; the opener answers only same-origin requests and
 * only for a token it actually holds.
 */

const REQUEST = 'webgis:story-owner-token:request';
const RESPONSE = 'webgis:story-owner-token:response';

interface TokenRequest {
  type: typeof REQUEST;
  sessionId: string;
  nonce: string;
}

interface TokenResponse {
  type: typeof RESPONSE;
  sessionId: string;
  nonce: string;
  token: string | null;
}

/** Opener side: answer Story-tab token requests. Returns an uninstaller. */
export function installStoryTokenResponder(
  getToken: (sessionId: string) => string | null,
): () => void {
  if (typeof window === 'undefined') return () => {};
  const onMessage = (event: MessageEvent) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data as Partial<TokenRequest> | null;
    if (!data || data.type !== REQUEST || typeof data.sessionId !== 'string' || typeof data.nonce !== 'string') return;
    const source = event.source as Window | null;
    if (!source || typeof source.postMessage !== 'function') return;
    const reply: TokenResponse = {
      type: RESPONSE,
      sessionId: data.sessionId,
      nonce: data.nonce,
      token: getToken(data.sessionId),
    };
    source.postMessage(reply, window.location.origin);
  };
  window.addEventListener('message', onMessage);
  return () => window.removeEventListener('message', onMessage);
}

/**
 * Story side: ask the opener for the owner token of `sessionId`. Resolves
 * null immediately when there is no same-origin opener, or after
 * `timeoutMs` when the opener does not answer (closed / old build).
 */
export function requestStoryOwnerToken(sessionId: string, timeoutMs = 1500): Promise<string | null> {
  if (typeof window === 'undefined' || !sessionId) return Promise.resolve(null);
  let opener: Window | null = null;
  try {
    opener = window.opener as Window | null;
  } catch {
    opener = null;
  }
  if (!opener || opener === window || typeof opener.postMessage !== 'function') return Promise.resolve(null);
  const nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return new Promise((resolve) => {
    const done = (token: string | null) => {
      window.removeEventListener('message', onMessage);
      clearTimeout(timer);
      resolve(token);
    };
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as Partial<TokenResponse> | null;
      if (!data || data.type !== RESPONSE || data.nonce !== nonce || data.sessionId !== sessionId) return;
      done(typeof data.token === 'string' && data.token ? data.token : null);
    };
    const timer = setTimeout(() => done(null), timeoutMs);
    window.addEventListener('message', onMessage);
    try {
      const req: TokenRequest = { type: REQUEST, sessionId, nonce };
      opener!.postMessage(req, window.location.origin);
    } catch {
      done(null);
    }
  });
}
