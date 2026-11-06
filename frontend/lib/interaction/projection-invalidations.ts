/** Persisted interaction invalidations; a UI may coalesce these without polling. */
import { devOnly } from '@/lib/utils/logger';

const listeners = new Set<(sessionId: string) => void>();
export function notifySituationInvalidated(sessionId: string): void {
  for (const listener of listeners) {
    try { listener(sessionId); } catch (error) { devOnly.warn("[situation] invalidation subscriber failed", error); }
  }
}
export function subscribeSituationInvalidation(listener: (sessionId: string) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
