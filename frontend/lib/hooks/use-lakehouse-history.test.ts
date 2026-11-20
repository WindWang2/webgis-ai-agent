import { describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { LAKEHOUSE_FAVORITES_KEY, useLakehouseHistory, type LakehouseQueryRecord } from './use-lakehouse-history';

function record(id: string, createdAt: number): LakehouseQueryRecord {
  return { id, kind: 'scan', label: id, request: { id }, createdAt, ref: id };
}

describe('useLakehouseHistory favorites cap', () => {
  it('keeps a new favorite by dropping the oldest when full', () => {
    const existing = Array.from({ length: 30 }, (_, i) => record(`old-${i}`, i + 1));
    sessionStorage.setItem(`${LAKEHOUSE_FAVORITES_KEY}:anonymous`, JSON.stringify(existing));
    const { result } = renderHook(() => useLakehouseHistory());
    expect(result.current.favorites).toHaveLength(30);
    act(() => {
      result.current.toggleFavorite(record('new', 10_000));
    });
    expect(result.current.favorites).toHaveLength(30);
    expect(result.current.favorites.some((row) => row.id === 'new')).toBe(true);
    expect(result.current.favorites.some((row) => row.id === 'old-0')).toBe(false);
  });
});
