/**
 * table-data（§10）契约：载荷规整 / 行 id 稳定 / 截断有界。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildTableModel,
  getCachedTableArtifact,
  loadTableArtifact,
  MAX_TABLE_ROWS,
  normalizeTablePayload,
  resetTableArtifactCache,
  resolveRowId,
} from './table-data';

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/transport', () => ({ apiFetch: apiFetchMock }));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'sid-table-test', ownerToken: null }),
}));
vi.mock('@/lib/utils/logger', () => ({ devOnly: { warn: vi.fn() } }));

const records = [
  { district: '武侯区', count: 10 },
  { district: '锦江区', count: 20 },
];

describe('resolveRowId', () => {
  it('uses FEATURE_ID_KEYS chain', () => {
    expect(resolveRowId({ osm_id: 123 })).toBe('123');
    expect(resolveRowId({ OBJECTID: 'A1' })).toBe('A1');
    expect(resolveRowId({}, 42)).toBe('42'); // 顶层 id 优先
  });

  it('falls back to content hash without ids', () => {
    const id = resolveRowId({ name: 'x' });
    expect(id.startsWith('h-')).toBe(true);
    expect(resolveRowId({ name: 'x' })).toBe(id); // 确定性
    expect(resolveRowId({ name: 'y' })).not.toBe(id);
  });
});

describe('buildTableModel', () => {
  it('derives columns from a bounded sample', () => {
    const model = buildTableModel(records);
    expect(model.columns).toEqual(['district', 'count']);
    expect(model.rows).toHaveLength(2);
    expect(model.truncated).toBe(false);
  });

  it('truncates above MAX_TABLE_ROWS with disclosure', () => {
    const big = Array.from({ length: MAX_TABLE_ROWS + 500 }, (_, i) => ({ i }));
    const model = buildTableModel(big);
    expect(model.rows.length).toBe(MAX_TABLE_ROWS);
    expect(model.truncated).toBe(true);
    expect(model.totalCount).toBe(MAX_TABLE_ROWS + 500);
  });

  it('rows share property references (zero copy)', () => {
    const model = buildTableModel(records);
    expect(model.rows[0].props).toBe(records[0]);
  });
});

describe('normalizeTablePayload', () => {
  it('accepts record arrays', () => {
    const model = normalizeTablePayload(records);
    expect(model?.rows).toHaveLength(2);
  });

  it('accepts {table: {columns, rows}} matrix form', () => {
    const model = normalizeTablePayload({
      table: { columns: ['district', 'count'], rows: [['武侯区', 10]] },
    });
    expect(model?.columns).toEqual(['district', 'count']);
    expect(model?.rows[0].props).toEqual({ district: '武侯区', count: 10 });
  });

  it('accepts {columns, rows} direct form', () => {
    const model = normalizeTablePayload({ columns: ['a'], rows: [{ a: 1 }] } as never);
    expect(model?.rows[0].props).toEqual({ a: 1 });
  });

  it('null for empty/invalid payloads', () => {
    expect(normalizeTablePayload(null)).toBeNull();
    expect(normalizeTablePayload({ table: [] })).toBeNull();
    expect(normalizeTablePayload({ table: { rows: [] } })).toBeNull();
  });
});

describe('table artifact cache bounds', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    resetTableArtifactCache();
  });

  it('evicts the oldest successful payload after 16 refs', async () => {
    apiFetchMock.mockResolvedValue({ table: records });
    for (let i = 0; i < 17; i++) {
      await expect(loadTableArtifact(`ref:table-${i}`)).resolves.toEqual(records);
    }

    expect(getCachedTableArtifact('ref:table-0')).toBeUndefined();
    expect(getCachedTableArtifact('ref:table-16')).toEqual(records);
    await expect(loadTableArtifact('ref:table-0')).resolves.toEqual(records);
    expect(apiFetchMock).toHaveBeenCalledTimes(18);
  });

  it('evicts failure timestamps with their payloads after 16 refs', async () => {
    apiFetchMock.mockRejectedValue(new Error('offline'));
    for (let i = 0; i < 17; i++) {
      await expect(loadTableArtifact(`ref:table-fail-${i}`)).resolves.toBeNull();
    }

    expect(getCachedTableArtifact('ref:table-fail-0')).toBeUndefined();
    expect(getCachedTableArtifact('ref:table-fail-16')).toBeNull();
    await expect(loadTableArtifact('ref:table-fail-0')).resolves.toBeNull();
    expect(apiFetchMock).toHaveBeenCalledTimes(18);
  });
});
