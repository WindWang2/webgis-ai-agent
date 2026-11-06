/**
 * Session identity holder — deliberately dependency-free.
 *
 * Holds the (sessionId, ownerToken) pair for the ACTIVE data-plane session.
 * Kept in a leaf module (no imports) so store slices can read the SEC-08
 * owner token without importing layer-data.ts (which depends on useHudStore
 * and would create a store → store cycle, #1109).
 */

const listeners = new Set<() => void>();
export function subscribeSessionIdentity(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

let _currentSessionId: string | undefined;
let _currentOwnerToken: string | null | undefined;

export function setSessionIdentity(
  sessionId: string | undefined,
  ownerToken: string | null | undefined
): void {
  if (_currentSessionId === sessionId && _currentOwnerToken === ownerToken) return;
  _currentSessionId = sessionId;
  _currentOwnerToken = ownerToken;
  listeners.forEach((listener) => listener());
}

export function getSessionIdentity(): {
  sessionId: string | undefined;
  ownerToken: string | null | undefined;
} {
  return { sessionId: _currentSessionId, ownerToken: _currentOwnerToken };
}
