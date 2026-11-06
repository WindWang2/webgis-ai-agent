'use client';

let pendingLocate: { sessionId: string; messageIndex: number; issuedAt: number } | null = null;
const LOCATE_TTL_MS = 15_000;

export function setPendingLocate(sessionId: string, messageIndex: number): void {
  pendingLocate = { sessionId, messageIndex, issuedAt: Date.now() };
}

export function consumePendingLocate(sessionId: string): number | null {
  if (!pendingLocate || pendingLocate.sessionId !== sessionId) return null;
  if (Date.now() - pendingLocate.issuedAt > LOCATE_TTL_MS) {
    pendingLocate = null;
    return null;
  }
  const index = pendingLocate.messageIndex;
  pendingLocate = null;
  return index;
}
