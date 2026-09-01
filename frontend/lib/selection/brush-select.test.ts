/**
 * Brush selection 纯函数（§11）：有界 id 投影 / 稳定 id 字段解析 / 屏幕矩形。
 */
import { describe, expect, it } from 'vitest';
import {
  isBrushRectViable,
  normalizeScreenRect,
  projectBrushHits,
  resolveIdField,
} from './brush-select';
import { MAX_SELECTED_IDS } from './selection-store';

describe('resolveIdField', () => {
  it('prefers property keys over top-level feature ids', () => {
    const field = resolveIdField([
      { id: 7, properties: { osm_id: 123 } },
      { id: 8, properties: { osm_id: 124 } },
    ]);
    expect(field).toBe('osm_id');
  });

  it('falls back to $id when only top-level ids exist', () => {
    expect(resolveIdField([{ id: 7, properties: {} }])).toBe('$id');
  });

  it('returns null when no stable identity exists', () => {
    expect(resolveIdField([{ properties: { name: 'x' } }])).toBeNull();
  });
});

describe('projectBrushHits', () => {
  it('bounds selected ids and flags truncation', () => {
    const hits = Array.from({ length: MAX_SELECTED_IDS + 40 }, (_, i) => ({
      id: i,
      properties: {},
    }));
    const proj = projectBrushHits(hits);
    expect(proj.selected_ids.length).toBe(MAX_SELECTED_IDS);
    expect(proj.truncated).toBe(true);
    expect(proj.matched_count).toBe(hits.length);
    expect(proj.id_field).toBe('$id');
  });

  it('dedups ids', () => {
    const proj = projectBrushHits([
      { id: 1, properties: {} },
      { id: 1, properties: {} },
      { id: 2, properties: {} },
    ]);
    expect(proj.selected_ids).toEqual(['1', '2']);
    // V4 review 语义：matched_count = 跨子层去重后的唯一要素数；截断只
    // 在唯一 id 超上限时发生（去重损耗不算截断）。
    expect(proj.truncated).toBe(false);
    expect(proj.matched_count).toBe(2);
  });

  it('no id field → empty ids + honest truncation flag', () => {
    const proj = projectBrushHits([{ properties: { name: 'x' } }]);
    expect(proj.id_field).toBeNull();
    expect(proj.selected_ids).toEqual([]);
    expect(proj.matched_count).toBe(1);
  });
});

describe('screen rect helpers', () => {
  it('normalizes drag direction', () => {
    const rect = normalizeScreenRect({ x: 100, y: 80 }, { x: 40, y: 20 });
    expect(rect).toEqual({ x: 40, y: 20, w: 60, h: 60 });
  });

  it('viability guard rejects accidental micro-drags', () => {
    expect(isBrushRectViable({ w: 2, h: 100 })).toBe(false);
    expect(isBrushRectViable({ w: 10, h: 10 })).toBe(true);
  });
});
