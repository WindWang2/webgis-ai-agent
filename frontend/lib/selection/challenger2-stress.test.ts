/**
 * Challenger 2 Empirical Stress & Edge Case Verification Suite
 * Covering:
 * 1. Cross-view spatial selection & brush selection
 * 2. Viewport context debounce, quantization & anti-jitter
 * 3. Table data normalization, virtualization, sorting & filtering
 * 4. Filter evidence status & closed operator evaluation
 * 5. Session reset cascade & cache clearance
 */

import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import {
  getSelection,
  getSelectionEpoch,
  getSelectionFilterExpression,
  getSelectionIdFilterExpression,
  publishSelection,
  resetSelectionStore,
  selectionEvents,
  MAX_SELECTED_IDS,
  MAX_SELECTION_PROPERTIES,
} from './selection-store';
import {
  isBrushRectViable,
  normalizeScreenRect,
  projectBrushHits,
  resolveIdField,
  BRUSH_MIN_PIXELS,
  type BrushHit,
} from './brush-select';
import {
  getViewportContext,
  getViewportEpoch,
  publishViewportContext,
  resetViewportContext,
} from './viewport-context';
import {
  clearFilterEvidence,
  deriveFilterEvidence,
  evaluateFilterBounded,
  getFilterEvidence,
  recordFilterEvidence,
  FILTER_SCAN_LIMIT,
} from '../layers/filter-evidence';
import {
  buildTableModel,
  normalizeTablePayload,
  resetTableArtifactCache,
  resolveRowId,
  MAX_TABLE_COLUMNS,
} from '../map-components/table-data';
import {
  setMapSpecSessionCursor,
} from '../mapspec/session-cursor';

describe('Challenger 2 — 1. Cross-View Spatial Selection & Brush Selection', () => {
  beforeEach(() => {
    resetSelectionStore();
    resetViewportContext();
  });

  describe('resolveIdField edge cases', () => {
    it('respects priority among multiple candidate ID keys', () => {
      const hits: BrushHit[] = [
        { properties: { fid: 101, OBJECTID: 202, id: 'id-303' } },
      ];
      expect(resolveIdField(hits)).toBe('id');

      const hits2: BrushHit[] = [
        { properties: { fid: 101, OBJECTID: 202 } },
      ];
      expect(resolveIdField(hits2)).toBe('OBJECTID');

      const hits3: BrushHit[] = [
        { properties: { feature_id: 'f-99', '@id': 'at-99' } },
      ];
      expect(resolveIdField(hits3)).toBe('@id');
    });

    it('handles sparse/ragged properties across multiple hit features', () => {
      const hits: BrushHit[] = [
        { properties: { name: 'A' } },
        { properties: { name: 'B', osm_id: 12345 } },
        { properties: { name: 'C' } },
      ];
      expect(resolveIdField(hits)).toBe('osm_id');
    });

    it('ignores empty strings and nulls in ID fields', () => {
      const hits: BrushHit[] = [
        { properties: { id: '', OBJECTID: null, osm_id: 555 } },
      ];
      expect(resolveIdField(hits)).toBe('osm_id');
    });

    it('resolves top-level feature ID as $id when properties lack candidate keys', () => {
      const hits: BrushHit[] = [
        { id: 9999, properties: { name: 'Lake' } },
        { id: 10000, properties: { name: 'River' } },
      ];
      expect(resolveIdField(hits)).toBe('$id');
    });

    it('returns null when neither properties nor top-level ID exist', () => {
      const hits: BrushHit[] = [
        { properties: { name: 'Unknown' } },
        { properties: { temperature: 24.5 } },
      ];
      expect(resolveIdField(hits)).toBeNull();
    });
  });

  describe('projectBrushHits stress & boundaries', () => {
    it('handles empty hit array gracefully', () => {
      const proj = projectBrushHits([]);
      expect(proj).toEqual({
        selected_ids: [],
        matched_count: 0,
        id_field: null,
        truncated: false,
      });
    });

    it('deduplicates thousands of repeated hits across multi-layer query results', () => {
      const hits: BrushHit[] = [];
      for (let i = 0; i < 500; i++) {
        for (let id = 1; id <= 10; id++) {
          hits.push({
            id: `feat-${id}`,
            properties: { id: `feat-${id}` },
            layer: { id: `layer-sub-${i % 5}` },
          });
        }
      }
      const proj = projectBrushHits(hits);
      expect(proj.matched_count).toBe(10);
      expect(proj.selected_ids.length).toBe(10);
      expect(proj.truncated).toBe(false);
      expect(proj.id_field).toBe('id');
    });

    it('caps selected_ids at MAX_SELECTED_IDS (50) and flags truncation correctly', () => {
      const hits: BrushHit[] = Array.from({ length: 250 }, (_, i) => ({
        id: i + 1,
        properties: { OBJECTID: i + 1 },
      }));
      const proj = projectBrushHits(hits);
      expect(proj.selected_ids.length).toBe(MAX_SELECTED_IDS);
      expect(proj.matched_count).toBe(250);
      expect(proj.truncated).toBe(true);
      expect(proj.id_field).toBe('OBJECTID');
      expect(proj.selected_ids[0]).toBe('1');
      expect(proj.selected_ids[49]).toBe('50');
    });

    it('handles $id extraction with mixed number/string IDs', () => {
      const hits: BrushHit[] = [
        { id: 101, properties: {} },
        { id: '202', properties: {} },
        { id: 303, properties: {} },
      ];
      const proj = projectBrushHits(hits);
      expect(proj.id_field).toBe('$id');
      expect(proj.selected_ids).toEqual(['101', '202', '303']);
    });
  });

  describe('Screen Rect normalization & viability', () => {
    it('normalizes drag gestures from all 4 diagonal directions', () => {
      expect(normalizeScreenRect({ x: 10, y: 20 }, { x: 100, y: 150 })).toEqual({ x: 10, y: 20, w: 90, h: 130 });
      expect(normalizeScreenRect({ x: 100, y: 150 }, { x: 10, y: 20 })).toEqual({ x: 10, y: 20, w: 90, h: 130 });
      expect(normalizeScreenRect({ x: 100, y: 20 }, { x: 10, y: 150 })).toEqual({ x: 10, y: 20, w: 90, h: 130 });
      expect(normalizeScreenRect({ x: 10, y: 150 }, { x: 100, y: 20 })).toEqual({ x: 10, y: 20, w: 90, h: 130 });
    });

    it('enforces BRUSH_MIN_PIXELS threshold strictly', () => {
      expect(BRUSH_MIN_PIXELS).toBe(6);
      expect(isBrushRectViable({ w: 5, h: 5 })).toBe(false);
      expect(isBrushRectViable({ w: 5, h: 6 })).toBe(false);
      expect(isBrushRectViable({ w: 6, h: 5 })).toBe(false);
      expect(isBrushRectViable({ w: 6, h: 6 })).toBe(true);
      expect(isBrushRectViable({ w: 0, h: 0 })).toBe(false);
    });
  });

  describe('Selection store limits, filter compilation & fast repeated publications', () => {
    it('handles 100 rapid brush drag publications without leaking memory or corrupting state', () => {
      for (let i = 0; i < 100; i++) {
        publishSelection('brush', {
          source: 'map',
          layer_id: 'poi-layer',
          selected_ids: [i, i + 1],
          id_field: 'fid',
          bbox: [104.0 + i * 0.001, 30.0, 104.1 + i * 0.001, 30.1],
          matched_count: 2,
        });
      }
      const sel = getSelection();
      expect(sel).toBeTruthy();
      expect(sel?.kind).toBe('brush');
      expect(sel?.selected_ids).toEqual(['99', '100']);
      expect(sel?.revision).toBe(100);
      expect(selectionEvents().length).toBeLessThanOrEqual(16);
    });

    it('strictly limits properties count and length', () => {
      const longString = 'x'.repeat(200);
      const props: Record<string, unknown> = {};
      for (let i = 0; i < 20; i++) {
        props[`prop_${i}`] = i < 10 ? longString : i;
      }
      publishSelection('select', {
        source: 'map',
        layer_id: 'l1',
        selected_ids: ['1'],
        properties: props,
      });
      const storedProps = getSelection()?.properties;
      expect(Object.keys(storedProps ?? {})).toHaveLength(MAX_SELECTION_PROPERTIES);
      expect(Object.values(storedProps ?? {})[0]).toHaveLength(64);
    });

    it('compiles MapLibre filter expression with type-safe string normalization', () => {
      publishSelection('select', {
        source: 'chart',
        layer_id: 'zones',
        selected_categories: ['Commercial', 'Residential'],
        filter_field: 'zone_type',
      });
      expect(getSelectionFilterExpression('zones')).toEqual([
        'in',
        ['to-string', ['get', 'zone_type']],
        ['literal', ['Commercial', 'Residential']],
      ]);

      publishSelection('brush', {
        source: 'map',
        layer_id: 'points',
        selected_ids: ['10', '20'],
        id_field: '$id',
      });
      expect(getSelectionIdFilterExpression('points')).toEqual([
        'in',
        ['to-string', ['id']],
        ['literal', ['10', '20']],
      ]);

      publishSelection('select', {
        source: 'table',
        layer_id: 'points',
        selected_ids: ['100'],
        id_field: 'OBJECTID',
      });
      expect(getSelectionIdFilterExpression('points')).toEqual([
        'in',
        ['to-string', ['get', 'OBJECTID']],
        ['literal', ['100']],
      ]);
    });
  });
});

describe('Challenger 2 — 2. Viewport Context Debounce, Quantization & Edge Cases', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetViewportContext();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('debounces rapid camera changes within 300ms', () => {
    for (let i = 0; i < 50; i++) {
      publishViewportContext([104.0 + i * 0.01, 30.0, 104.1 + i * 0.01, 30.1], 10 + i * 0.05);
    }
    expect(getViewportContext()).toBeNull();

    vi.advanceTimersByTime(200);
    expect(getViewportContext()).toBeNull();

    vi.advanceTimersByTime(150);
    const ctx = getViewportContext();
    expect(ctx).toBeTruthy();
    expect(ctx?.bbox[0]).toBeCloseTo(104.49, 2);
    expect(ctx?.bbox[1]).toBe(30.0);
    expect(ctx?.bbox[2]).toBeCloseTo(104.59, 2);
    expect(ctx?.bbox[3]).toBe(30.1);
    expect(ctx?.zoom).toBeCloseTo(12.45, 2);
  });

  it('quantizes micro-jitter (sub-10m) and suppresses duplicate emissions', () => {
    publishViewportContext([104.12345, 30.12345, 104.56789, 30.56789], 12.34);
    vi.advanceTimersByTime(350);
    const gen1 = getViewportContext()?.generation;
    expect(gen1).toBeDefined();

    publishViewportContext([104.12346, 30.12345, 104.56788, 30.56789], 12.341);
    vi.advanceTimersByTime(350);
    expect(getViewportContext()?.generation).toBe(gen1);

    publishViewportContext([104.13345, 30.12345, 104.56789, 30.56789], 12.34);
    vi.advanceTimersByTime(350);
    expect(getViewportContext()?.generation).toBeGreaterThan(gen1!);
  });

  it('rejects invalid, NaN, and Infinite bboxes immediately', () => {
    expect(publishViewportContext([NaN, 30, 104, 31], 10, 0)).toBe(false);
    expect(publishViewportContext([104, -Infinity, 104.1, 30.1], 10, 0)).toBe(false);
    expect(publishViewportContext([104, 30, 104.1, 30.1], NaN, 0)).toBe(false);
    expect(publishViewportContext([104, 30, 104.1, 30.1], Infinity, 0)).toBe(false);
    expect(getViewportContext()).toBeNull();
  });

  it('cancels pending debounce when resetViewportContext is called during flight', () => {
    publishViewportContext([104.0, 30.0, 104.1, 30.1], 10);
    expect(getViewportContext()).toBeNull();

    resetViewportContext();

    vi.advanceTimersByTime(500);
    expect(getViewportContext()).toBeNull();
  });
});

describe('Challenger 2 — 3. Table Panel Data, Normalization, Sorting & Filtering', () => {
  beforeEach(() => {
    resetTableArtifactCache();
  });

  describe('normalizeTablePayload robustness on empty/malformed artifacts', () => {
    it('returns null for null, undefined, and empty objects', () => {
      expect(normalizeTablePayload(null)).toBeNull();
      expect(normalizeTablePayload(undefined as never)).toBeNull();
      expect(normalizeTablePayload({} as never)).toBeNull();
      expect(normalizeTablePayload([])).toBeNull();
    });

    it('returns null for table with empty rows', () => {
      expect(normalizeTablePayload({ table: { columns: ['A', 'B'], rows: [] } })).toBeNull();
      expect(normalizeTablePayload({ columns: ['A'], rows: [] } as never)).toBeNull();
    });

    it('handles matrix table payloads with column names and array rows', () => {
      const payload = {
        table: {
          columns: ['code', 'name', 'pop'],
          rows: [
            ['510107', '武侯区', 1200000],
            ['510104', '锦江区', 900000],
          ],
        },
      };
      const model = normalizeTablePayload(payload);
      expect(model).toBeTruthy();
      expect(model?.columns).toEqual(['code', 'name', 'pop']);
      expect(model?.rows).toHaveLength(2);
      expect(model?.rows[0].props).toEqual({
        code: '510107',
        name: '武侯区',
        pop: 1200000,
      });
    });

    it('handles record arrays directly', () => {
      const records = [
        { id: 'r1', city: 'Chengdu', temp: 22 },
        { id: 'r2', city: 'Chongqing', temp: 28 },
      ];
      const model = normalizeTablePayload(records);
      expect(model).toBeTruthy();
      expect(model?.columns).toEqual(['id', 'city', 'temp']);
      expect(model?.rows[0].rowId).toBe('r1');
    });

    it('truncates columns at MAX_TABLE_COLUMNS (32)', () => {
      const bigRow: Record<string, unknown> = {};
      for (let i = 0; i < 50; i++) bigRow[`col_${i}`] = i;
      const model = normalizeTablePayload([bigRow]);
      expect(model?.columns.length).toBe(MAX_TABLE_COLUMNS);
    });
  });

  describe('resolveRowId and duplicate handling in buildTableModel', () => {
    it('uses topLevelIds when provided and falls back to properties', () => {
      const records = [{ name: 'A' }, { name: 'B' }];
      const model = buildTableModel(records, undefined, ['top-1', 42]);
      expect(model.rows[0].rowId).toBe('top-1');
      expect(model.rows[1].rowId).toBe('42');
    });

    it('disambiguates duplicated IDs with suffix to maintain React key uniqueness', () => {
      const records = [
        { id: 'dup', name: 'Item 1' },
        { id: 'dup', name: 'Item 2' },
        { id: 'dup', name: 'Item 3' },
      ];
      const model = buildTableModel(records);
      expect(model.rows[0].rowId).toBe('dup');
      expect(model.rows[1].rowId).toBe('dup#1');
      expect(model.rows[2].rowId).toBe('dup#2');
    });

    it('generates deterministic content hash for records without ID', () => {
      const rec = { district: 'Wuhou', value: 99 };
      const id1 = resolveRowId(rec);
      const id2 = resolveRowId(rec);
      expect(id1.startsWith('h-')).toBe(true);
      expect(id1).toBe(id2);
    });
  });
});

describe('Challenger 2 — 4. Filter Evidence Badges & Evaluation', () => {
  beforeEach(() => {
    clearFilterEvidence();
  });

  const sampleFeatures = [
    { properties: { district: '武侯区', score: 85, active: true }, geometry: { type: 'Point' } },
    { properties: { district: '锦江区', score: 92, active: false }, geometry: { type: 'Point' } },
    { properties: { district: '青羊区', score: 70, active: true }, geometry: { type: 'Polygon' } },
  ];

  describe('evaluateFilterBounded operator fidelity', () => {
    it('evaluates comparison operators (==, !=, >, >=, <, <=) with strict type rules', () => {
      const f = sampleFeatures[0];
      expect(evaluateFilterBounded(['==', ['get', 'district'], '武侯区'], f)).toBe(true);
      expect(evaluateFilterBounded(['==', ['get', 'district'], '青羊区'], f)).toBe(false);
      expect(evaluateFilterBounded(['!=', ['get', 'district'], '青羊区'], f)).toBe(true);
      expect(evaluateFilterBounded(['>', ['get', 'score'], 80], f)).toBe(true);
      expect(evaluateFilterBounded(['<=', ['get', 'score'], 85], f)).toBe(true);
      expect(evaluateFilterBounded(['<', ['get', 'score'], 85], f)).toBe(false);

      expect(evaluateFilterBounded(['>', ['get', 'district'], 50], f)).toBe(false);
    });

    it('evaluates $type correctly for Point and Polygon', () => {
      expect(evaluateFilterBounded(['==', '$type', 'Point'], sampleFeatures[0])).toBe(true);
      expect(evaluateFilterBounded(['==', '$type', 'Polygon'], sampleFeatures[0])).toBe(false);
      expect(evaluateFilterBounded(['==', '$type', 'Polygon'], sampleFeatures[2])).toBe(true);
    });

    it('evaluates in operator with strict equality against literal array', () => {
      const inFilter = ['in', ['get', 'district'], ['literal', ['武侯区', '高新区']]];
      expect(evaluateFilterBounded(inFilter, sampleFeatures[0])).toBe(true);
      expect(evaluateFilterBounded(inFilter, sampleFeatures[1])).toBe(false);
    });

    it('evaluates logical operators (all, any, !)', () => {
      const allFilter = [
        'all',
        ['==', ['get', 'active'], true],
        ['>=', ['get', 'score'], 80],
      ];
      expect(evaluateFilterBounded(allFilter, sampleFeatures[0])).toBe(true);
      expect(evaluateFilterBounded(allFilter, sampleFeatures[1])).toBe(false);
      expect(evaluateFilterBounded(allFilter, sampleFeatures[2])).toBe(false);

      const anyFilter = [
        'any',
        ['==', ['get', 'district'], '青羊区'],
        ['>', ['get', 'score'], 90],
      ];
      expect(evaluateFilterBounded(anyFilter, sampleFeatures[1])).toBe(true);
      expect(evaluateFilterBounded(anyFilter, sampleFeatures[2])).toBe(true);
      expect(evaluateFilterBounded(anyFilter, sampleFeatures[0])).toBe(false);

      expect(evaluateFilterBounded(['!', ['==', ['get', 'active'], true]], sampleFeatures[1])).toBe(true);
    });

    it('returns null (unknown) for unhandled or invalid operators without throwing', () => {
      expect(evaluateFilterBounded(['within', ['get', 'geo']], sampleFeatures[0])).toBeNull();
      expect(evaluateFilterBounded([] as never, sampleFeatures[0])).toBeNull();
      expect(evaluateFilterBounded(null as never, sampleFeatures[0])).toBeNull();
    });
  });

  describe('deriveFilterEvidence status contract', () => {
    it('returns inactive when sublayerFilters is empty', () => {
      const ev = deriveFilterEvidence({
        layerId: 'layer-1',
        sublayerFilters: [],
        features: sampleFeatures,
      });
      expect(ev.status).toBe('inactive');
    });

    it('returns active with matched_count when items pass filter', () => {
      const ev = deriveFilterEvidence({
        layerId: 'layer-1',
        sublayerFilters: [['==', ['get', 'district'], '武侯区']],
        features: sampleFeatures,
      });
      expect(ev.status).toBe('active');
      expect(ev.matched_count).toBe(1);
      expect(ev.scanned).toBe(3);
    });

    it('returns empty when 0 features pass filter', () => {
      const ev = deriveFilterEvidence({
        layerId: 'layer-1',
        sublayerFilters: [['==', ['get', 'district'], '不存在区']],
        features: sampleFeatures,
      });
      expect(ev.status).toBe('empty');
      expect(ev.matched_count).toBe(0);
    });

    it('returns invalid when filter references non-existent property field', () => {
      const ev = deriveFilterEvidence({
        layerId: 'layer-1',
        sublayerFilters: [['==', ['get', 'non_existent_column'], 'foo']],
        features: sampleFeatures,
      });
      expect(ev.status).toBe('invalid');
    });

    it('returns unknown when features exceed FILTER_SCAN_LIMIT (20000)', () => {
      const hugeFeatures = Array.from({ length: FILTER_SCAN_LIMIT + 10 }, () => ({
        properties: { district: '武侯区' },
        geometry: { type: 'Point' },
      }));
      const ev = deriveFilterEvidence({
        layerId: 'layer-1',
        sublayerFilters: [['==', ['get', 'district'], '武侯区']],
        features: hugeFeatures,
      });
      expect(ev.status).toBe('unknown');
      expect(ev.scanned).toBe(FILTER_SCAN_LIMIT + 10);
    });
  });

  describe('recordFilterEvidence pruning & bounds', () => {
    it('prunes layers not in pruneTo set to prevent ghost badges', () => {
      recordFilterEvidence([
        { layerId: 'l1', evidence: { status: 'active', at: 1 } },
        { layerId: 'l2', evidence: { status: 'empty', at: 1 } },
      ]);
      expect(getFilterEvidence('l1')).toBeTruthy();
      expect(getFilterEvidence('l2')).toBeTruthy();

      recordFilterEvidence([], { pruneTo: new Set(['l1']) });
      expect(getFilterEvidence('l1')).toBeTruthy();
      expect(getFilterEvidence('l2')).toBeNull();
    });

    it('bounds evidenceByLayer to MAX_FILTER_EVIDENCE_LAYERS (128)', () => {
      const entries = Array.from({ length: 150 }, (_, i) => ({
        layerId: `layer-${i}`,
        evidence: { status: 'active' as const, at: Date.now() },
      }));
      recordFilterEvidence(entries);
      expect(getFilterEvidence('layer-149')).toBeTruthy();
      expect(getFilterEvidence('layer-0')).toBeNull();
    });
  });
});

describe('Challenger 2 — 5. Session Reset Cascade & Cache Clearance', () => {
  it('resets selection store, viewport context, filter evidence, and table cache on session switch', async () => {
    publishSelection('select', {
      source: 'map',
      layer_id: 'poi',
      selected_ids: ['100'],
    });
    publishViewportContext([104, 30, 105, 31], 11, 0);
    recordFilterEvidence([
      { layerId: 'poi', evidence: { status: 'active', at: Date.now() } },
    ]);

    expect(getSelection()).toBeTruthy();
    expect(getViewportContext()).toBeTruthy();
    expect(getFilterEvidence('poi')).toBeTruthy();

    const oldEpoch = getSelectionEpoch();
    const oldViewportEpoch = getViewportEpoch();

    setMapSpecSessionCursor('session-new-456', 0, null);

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(getSelection()).toBeNull();
    expect(getViewportContext()).toBeNull();
    expect(getFilterEvidence('poi')).toBeNull();
    expect(getSelectionEpoch()).toBeGreaterThan(oldEpoch);
    expect(getViewportEpoch()).toBeGreaterThan(oldViewportEpoch);
  });
});
