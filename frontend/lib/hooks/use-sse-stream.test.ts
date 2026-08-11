import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  useSSEStream,
  buildSelectedFeatureSnapshot,
  resolveParentLayerId,
} from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import type { SelectedFeatureInfo } from '@/lib/store/hud-types';

// ── Mocks ────────────────────────────────────────────────────────────────
// use-sse-stream consumes the bridge for aiStatus + send; we spy on send to
// capture the mapState payload (the FE-4 contract under test).
const bridgeMock = vi.hoisted(() => ({
  send: vi.fn().mockResolvedValue(undefined),
  aiStatus: 'idle',
}));

vi.mock('./useMapBridge', () => ({ useMapBridge: () => bridgeMock }));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const setSessionId = vi.fn();
const dispatchAction = vi.fn();
const getMapSnapshot = vi.fn(() => null);

function renderStream() {
  return renderHook(() =>
    useSSEStream(
      'sid-fe4',
      setSessionId,
      { current: 'sid-fe4' },
      dispatchAction,
      getMapSnapshot,
      null,
      { current: null },
    ),
  );
}

async function sendAndGetMapState() {
  const { result } = renderStream();
  await act(async () => {
    await result.current.handleSend('hi');
  });
  return bridgeMock.send.mock.calls[0][1] as Record<string, unknown>;
}

describe('resolveParentLayerId (FE-4 design §7)', () => {
  it('longest-prefix match fixes poi vs poi_schools mis-attribution', () => {
    expect(resolveParentLayerId('poi_schools__fill', ['poi', 'poi_schools'])).toBe('poi_schools');
    expect(resolveParentLayerId('poi__point', ['poi', 'poi_schools'])).toBe('poi');
  });

  it('matches ref/custom parent ids and falls back to stripping __sub', () => {
    expect(resolveParentLayerId('ref:geojson-abc__point', ['ref:geojson-abc'])).toBe(
      'ref:geojson-abc',
    );
    // parent layer gone from the project → strip the sublayer suffix, never
    // emit the raw `__` id into the prompt path
    expect(resolveParentLayerId('poi_schools__fill', [])).toBe('poi_schools');
    expect(resolveParentLayerId('ref:x__point', [])).toBe('ref:x');
    // already a parent id / unknown ids pass through unchanged
    expect(resolveParentLayerId('poi_schools', ['poi', 'poi_schools'])).toBe('poi_schools');
    expect(resolveParentLayerId('custom-9', [])).toBe('custom-9');
    expect(resolveParentLayerId('', ['a'])).toBe('');
  });
});

describe('buildSelectedFeatureSnapshot (FE-4 design §7)', () => {
  const base: SelectedFeatureInfo = {
    layerId: 'ref:geojson-abc__point',
    layerName: '测试层',
    refId: 'ref:geojson-abc',
    point: [116.4, 39.9],
    properties: {},
    selectedAt: 1700000000000,
  };

  it('bounds properties to ≤5 scalar entries, dropping nested payloads', () => {
    const sel: SelectedFeatureInfo = {
      ...base,
      properties: {
        name: '第一区',
        area_km2: 12.5,
        geometry: { type: 'Polygon', coordinates: [[[116.3, 39.8]]] },
        tags: { a: 1 },
        long_val: 'x'.repeat(5000),
        extra1: 'v1',
        extra2: 'v2',
        extra3: 'v3',
      },
    };
    const snap = buildSelectedFeatureSnapshot(sel, ['ref:geojson-abc']);
    const props = snap.properties as Record<string, unknown>;
    expect(Object.keys(props).length).toBeLessThanOrEqual(5);
    expect(props).not.toHaveProperty('geometry');
    expect(props).not.toHaveProperty('tags');
    expect(props.name).toBe('第一区');
    expect(props.long_val).toHaveLength(60); // 59 chars + ellipsis
    expect(props.long_val.endsWith('…')).toBe(true);
  });

  it('resolves parent layer id and keeps bbox/feature identity', () => {
    const snap = buildSelectedFeatureSnapshot(
      { ...base, featureId: 42, bbox: [116.3, 39.8, 116.5, 40.0] },
      ['ref:geojson-abc'],
    );
    expect(snap.layer_id).toBe('ref:geojson-abc'); // parent, NOT the __ sublayer id
    expect(snap.feature_id).toBe(42);
    expect(snap.bbox).toEqual([116.3, 39.8, 116.5, 40.0]);
    expect(snap.point).toEqual([116.4, 39.9]);
  });

  it('feature identity falls back to id-like property, then stable content hash', () => {
    const byProp = buildSelectedFeatureSnapshot(
      { ...base, properties: { OBJECTID: 7, name: 'n' } },
      [],
    );
    expect(byProp.feature_id).toBe(7);

    const a = buildSelectedFeatureSnapshot({ ...base, properties: { name: 'n' } }, []);
    const b = buildSelectedFeatureSnapshot({ ...base, properties: { name: 'n' } }, []);
    expect(typeof a.feature_id).toBe('string');
    expect(a.feature_id).toMatch(/^h-[0-9a-f]{8}$/); // djb2 content hash
    expect(a.feature_id).toBe(b.feature_id); // stable across calls
  });

  it('omits bad bbox silently', () => {
    const snap = buildSelectedFeatureSnapshot(
      { ...base, bbox: [1, 2, 3] as unknown as [number, number, number, number] },
      [],
    );
    expect(snap.bbox).toBeNull();
  });
});

describe('useSSEStream mapState snapshot (FE-4 design §7)', () => {
  beforeEach(() => {
    bridgeMock.send.mockClear();
    useHudStore.setState({
      selectedFeature: null,
      focusLayerId: null,
      layers: [],
      baseLayer: 'OSM 地图',
      viewport: { center: [0, 0], zoom: 5, bearing: 0, pitch: 0 },
      is3D: false,
    });
  });

  it('selected_feature is bounded (parent id + ≤5 props) and focus_layer_id flows', async () => {
    useHudStore.setState({
      selectedFeature: {
        layerId: 'ref:geojson-abc__point', // sublayer id must resolve to parent
        layerName: '测试层',
        refId: 'ref:geojson-abc',
        point: [116.4, 39.9],
        properties: {
          name: '第一区',
          area_km2: 12.5,
          geometry: { type: 'Polygon', coordinates: [[[116.3, 39.8]]] },
          tags: { a: 1 },
          extra1: 'v1',
          extra2: 'v2',
          extra3: 'v3',
        },
        selectedAt: 1700000000000,
        featureId: 42,
        bbox: [116.3, 39.8, 116.5, 40.0],
      },
      focusLayerId: 'ref:geojson-abc',
      layers: [{ id: 'ref:geojson-abc' }] as any,
    });

    const mapState = await sendAndGetMapState();
    const sel = mapState.selected_feature as Record<string, unknown>;

    expect(sel.layer_id).toBe('ref:geojson-abc'); // parent, never `__point`
    expect(sel.feature_id).toBe(42);
    expect(sel.bbox).toEqual([116.3, 39.8, 116.5, 40.0]);
    expect((sel.properties as Record<string, unknown>)).not.toHaveProperty('geometry');
    expect((sel.properties as Record<string, unknown>)).not.toHaveProperty('tags');
    expect(Object.keys(sel.properties as Record<string, unknown>).length).toBeLessThanOrEqual(5);
    expect((sel.properties as Record<string, unknown>).name).toBe('第一区');
    expect(mapState.focus_layer_id).toBe('ref:geojson-abc');
    // whole snapshot stays small — no raw feature payload can ride along
    expect(JSON.stringify(sel).length).toBeLessThan(1500);
  });

  it('omits selection/focus entirely when absent', async () => {
    const mapState = await sendAndGetMapState();
    expect(mapState.selected_feature).toBeNull();
    expect(mapState.focus_layer_id).toBeNull();
  });
});
