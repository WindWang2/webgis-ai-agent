/**
 * Turn generation for map side effects that outlive the SSE reader.
 *
 * Cancel and the next send bump this epoch. Layer fetches and observation
 * repairs subscribe and drop work that belongs to a previous turn.
 */
let epoch = 0;
const listeners = new Set<() => void>();

export function currentTurnEpoch(): number {
  return epoch;
}

export function bumpTurnEpoch(): number {
  epoch += 1;
  for (const listener of listeners) {
    listener();
  }
  return epoch;
}

export function subscribeTurnEpoch(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
