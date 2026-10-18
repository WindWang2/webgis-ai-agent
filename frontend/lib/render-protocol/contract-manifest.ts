/**
 * Render protocol contract manifest —— Python↔TS 词表/预算三重锁的 TS 侧。
 *
 * 三重锁（#1617 contract-manifest 同款模式）：
 *
 *   app/lib/cartography/{render_work_envelope,render_apply_ack,
 *   render_perf_probes,renderer_capability}.py（后端单一权威）
 *     ≡ tests/cartography/render_work_corpus/contract_manifest.json（生成物）
 *     ≡ 本模块（TS 镜像）
 *
 * 任何一侧漂移由双侧 drift test 爆（corpus.golden.test.ts /
 * test_render_work_corpus_v1.py）。**手改本文件 = 伪造一致性** —— 只能经
 * `tests/cartography/render_work_corpus/generate.py` 再生。
 */

export const RENDER_WORK_MANIFEST = {
  schema_version: 'render_work.v1',
  max_data_dependencies: 64,
  max_layer_work: 128,
  max_component_work: 32,
  max_diagnostics: 8,
  source_kinds: [
    'geojson', 'vector', 'raster', 'raster-dem',
    'data_fabric', 'wms', 'wmts', 'pmtiles',
  ],
} as const;

export const APPLY_ACK_MANIFEST = {
  schema_version: 'render_apply_ack.v1',
  statuses: ['applied', 'partial', 'failed'] as const,
  entry_statuses: ['applied', 'failed', 'skipped', 'pending', 'stale'] as const,
  reason_codes: [
    'missing_after_apply',
    'unsupported_layer_type',
    'source_unresolved',
    'style_diverged',
    'apply_error',
    'user_pending',
    'budget_degraded',
    'stale_data_revision',
  ] as const,
  max_layers: 64,
  max_components: 32,
  max_sources: 64,
} as const;

export const RENDER_PERF_MANIFEST = {
  schema_version: 'render_perf_probes.v1',
} as const;

export const RENDERER_CAPABILITY_MANIFEST = {
  schema_version: 'renderer_capability.v1',
  levels: ['full', 'partial', 'none'] as const,
  source_type_levels: {
    geojson: 'full',
    vector: 'full',
    raster: 'full',
    'raster-dem': 'full',
    data_fabric: 'partial',
    wms: 'none',
    wmts: 'none',
    pmtiles: 'none',
  } as const,
  feature_support: {
    point_clustering: true,
    heatmap_density: true,
    label_placement: true,
    terrain: true,
    fill_extrusion_3d: true,
    semantic_zoom_representation: false,
    generalization: false,
    export_svg: true,
    export_pdf: true,
  } as const,
} as const;
