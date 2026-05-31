'use client';

/**
 * Monotonic message ID generator.
 * Avoids the Date.now()+1 collision risk when handleSend is called
 * multiple times within the same millisecond.
 */
export function createMessageIdGenerator() {
  let counter = 0;
  return {
    next(): string {
      counter += 1;
      return `${Date.now()}-${counter}`;
    },
  };
}
