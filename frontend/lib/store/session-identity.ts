/**
 * Session identity holder — deliberately dependency-free.
 *
 * Holds the (sessionId, ownerToken) pair for the ACTIVE data-plane session.
 * Kept in a leaf module (no imports) so store slices can read the SEC-08
 * owner token without importing layer-data.ts (which depends on useHudStore
 * and would create a store → store cycle, #1109).
 */

let _currentSessionId: string | undefined;
let _currentOwnerToken: string | null | undefined;

export function setSessionIdentity(
  sessionId: string | undefined,
  ownerToken: string | null | undefined
): void {
  _currentSessionId = sessionId;
  _currentOwnerToken = ownerToken;
}

export function getSessionIdentity(): {
  sessionId: string | undefined;
  ownerToken: string | null | undefined;
} {
  return { sessionId: _currentSessionId, ownerToken: _currentOwnerToken };
}
