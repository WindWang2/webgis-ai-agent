/**
 * 大数据契约（§35-§37 / Scenario E）：150k+ 特征下，选择/过滤证据/表格的
 * 关键投影保持有界 —— 不做全量坐标遍历、DOM 行数与数据量解耦。
 *
 * 确定性合同（非 wall-clock）：计数断言 + 有界上限断言。
 */
import { describe, expect, it } from 'vitest';
import { buildTableModel } from '@/lib/map-components/table-data';
import { deriveFilterEvidence, FILTER_SCAN_LIMIT } from '@/lib/layers/filter-evidence';
import { projectBrushHits } from '@/lib/selection/brush-select';
import { MAX_SELECTED_IDS } from '@/lib/selection/selection-store';
import { deriveLayerStatus } from '@/lib/layers/layer-status';

const N = 150_000;

function makeFeatures(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    type: 'Feature',
    id: i,
    properties: { osm_id: i, district: `区${i % 20}` },
    geometry: { type: 'Point', coordinates: [104 + (i % 100) * 0.001, 30.6 + (i % 80) * 0.001] },
  }));
}

describe('Scenario E — 150k feature contracts', () => {
  it('table model truncates at 50k rows (no 150k row rendering surface)', () => {
    const features = makeFeatures(N);
    const model = buildTableModel(features.map((f) => f.properties));
    expect(model.rows.length).toBeLessThanOrEqual(50000);
    expect(model.truncated).toBe(true);
    expect(model.totalCount).toBe(N);
  });

  it('filter evidence refuses to scan 150k features (unknown, not a 150k scan)', () => {
    const features = makeFeatures(N);
    const evidence = deriveFilterEvidence({
      layerId: 'poi',
      sublayerFilters: [['in', ['get', 'district'], ['literal', ['区1']]]],
      features,
    });
    expect(evidence.status).toBe('unknown');
    expect(evidence.scanned).toBe(N);
    expect(evidence.matched_count).toBeUndefined();
    expect(FILTER_SCAN_LIMIT).toBeLessThan(N);
  });

  it('brush projection stays bounded regardless of hit count', () => {
    const hits = makeFeatures(N);
    const proj = projectBrushHits(hits as never);
    expect(proj.selected_ids.length).toBeLessThanOrEqual(MAX_SELECTED_IDS);
    expect(proj.truncated).toBe(true);
    expect(proj.matched_count).toBe(N);
  });

  it('layer status derivation is feature-count-independent (metadata-only inputs)', () => {
    const layer = {
      id: 'poi', visible: true, _refId: 'ref:geojson-x',
      source: { type: 'FeatureCollection', features: makeFeatures(1000) },
    };
    // O(layers) 派生 —— 输入只有行元数据 + 证据，不触碰 features。
    const status = deriveLayerStatus({ layer: layer as never, evidence: null });
    expect(status).toBe('ready');
  });
});
