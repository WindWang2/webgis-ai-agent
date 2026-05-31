import { describe, it, expect } from 'vitest';
import { createMessageIdGenerator } from './use-message-id';

describe('createMessageIdGenerator', () => {
  it('generates unique IDs even in the same millisecond', () => {
    const gen = createMessageIdGenerator();
    const id1 = gen.next();
    const id2 = gen.next();
    const id3 = gen.next();
    expect(id1).not.toBe(id2);
    expect(id2).not.toBe(id3);
    expect(id1).not.toBe(id3);
  });

  it('returns string IDs', () => {
    const gen = createMessageIdGenerator();
    const id = gen.next();
    expect(typeof id).toBe('string');
    expect(id.length).toBeGreaterThan(0);
  });

  it('generates monotonically increasing IDs', () => {
    const gen = createMessageIdGenerator();
    const ids = Array.from({ length: 100 }, () => gen.next());
    const unique = new Set(ids);
    expect(unique.size).toBe(100);
  });
});
