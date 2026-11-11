/**
 * G26：export_vector_pdf → 统一混合编译路径的前端路由测试。
 *
 * 数值栅格层在场时 POST /api/v1/export/mixed（服务端冻结权威 MapSpec）；
 * 无栅格层时既有 vector-pdf 载荷路径零变化。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const cursorState = {
  committed: { version: '1.9', layers: [] } as Record<string, unknown> | null,
  sessionId: 'sess-g26' as string | undefined,
  pending: {} as Record<string, { visible?: boolean }>,
  removed: [] as string[],
};

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => cursorState.committed,
  getMapSpecSessionCursor: () => ({ sessionId: cursorState.sessionId }),
  getPendingPresentation: () => cursorState.pending,
  getPendingRemoved: () => cursorState.removed,
}));

vi.mock('@/lib/map-kit/exporter', () => ({
  composeExportSpec: vi.fn(async (committed: unknown) => ({
    ...(committed as Record<string, unknown>),
  })),
  MapExporterEngine: { export: vi.fn(async () => ({ ok: true })) },
}));

vi.mock('@/lib/mapspec/ref-source-resolver', () => ({
  injectResolvedRefSources: vi.fn(
    (spec: Record<string, unknown>) => ({ ...spec }) as never,
  ),
}));

const mixedCalls: Array<Record<string, unknown>> = [];
const vectorCalls: Array<Record<string, unknown>> = [];

vi.mock('@/lib/api/publication', () => ({
  exportVectorPdf: vi.fn(async (params: Record<string, unknown>) => {
    vectorCalls.push(params);
    return {
      success: true,
      filename: 'map_vector_x.pdf',
      url: '/api/v1/export/download/map_vector_x.pdf',
      format: 'pdf',
      vector: true,
      pages: 1,
      frames_rendered: 1,
      frames_skipped: 0,
    };
  }),
  vectorPdfErrorCode: vi.fn(
    (err: { code?: string | null }) => err.code ?? null,
  ),
  exportMixedMap: vi.fn(async (params: Record<string, unknown>) => {
    mixedCalls.push(params);
    return {
      success: true,
      filename: 'mixed_map_x.pdf',
      url: '/api/v1/export/download/mixed_map_x.pdf',
      manifest_url: '/api/v1/export/download/mixed_map_x.pdf.manifest.json',
      format: 'pdf',
      media_type: 'application/pdf',
      bytes: 204800,
      mapspec_revision: 7,
      partial: false,
      omitted_layers: [],
      raster_layers: [
        {
          layer_id: 'raster-layer',
          style_id: 'st1',
          overlap: 'full',
          embedded: true,
          width: 1024,
          height: 768,
        },
      ],
      renderer: { engine: 'weasyprint', pages: 1 },
      checksum: 'sha256:abc',
    };
  }),
  mixedExportErrorCode: vi.fn(
    (err: { body?: { detail?: { code?: string } | string }; status?: number }) => {
      const detail = err?.body?.detail;
      if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
        return detail.code;
      }
      if (typeof detail === 'string' && detail) return detail;
      return null;
    },
  ),
}));

import { exportMixedMap } from '@/lib/api/publication';
import {
  hasNumericRasterLayers,
  runVectorPdfExport,
} from './publication-export';

const mockedMixed = vi.mocked(exportMixedMap);

function makeHud() {
  return {
    messages: [] as (string | null)[],
    exports: [] as Record<string, unknown>[],
    setPendingSystemMessage(msg: string | null) {
      this.messages.push(msg);
    },
    addExport(item: Record<string, unknown>) {
      this.exports.push(item);
    },
  };
}

const SPEC_WITH_RASTER_STYLE = {
  version: '1.9',
  sources: {
    r: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } },
  },
  layers: [
    {
      id: 'vec',
      source: 'r',
      type: 'circle',
      paint: {},
    },
    {
      id: 'ras',
      source: 'rs',
      type: 'raster',
      paint: {},
      raster_style: { mode: 'single', bands: [1] },
    },
  ],
} as unknown as Record<string, unknown>;

const SPEC_WITH_NUMERIC_SOURCE_ONLY = {
  version: '1.9',
  sources: {
    rs: { type: 'numeric_raster', refId: 'ref:raster/a' },
  },
  layers: [{ id: 'ras', source: 'rs', type: 'raster', paint: {} }],
} as unknown as Record<string, unknown>;

const SPEC_VECTOR_ONLY = {
  version: '1.9',
  sources: {
    r: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } },
  },
  layers: [{ id: 'vec', source: 'r', type: 'circle', paint: {} }],
} as unknown as Record<string, unknown>;

describe('hasNumericRasterLayers（G26 检测）', () => {
  it('raster_style 在场 → true', () => {
    expect(hasNumericRasterLayers(SPEC_WITH_RASTER_STYLE)).toBe(true);
  });

  it('numeric_raster 源（无 raster_style）→ true', () => {
    expect(hasNumericRasterLayers(SPEC_WITH_NUMERIC_SOURCE_ONLY)).toBe(true);
  });

  it('纯矢量 → false；空 layers → false', () => {
    expect(hasNumericRasterLayers(SPEC_VECTOR_ONLY)).toBe(false);
    expect(hasNumericRasterLayers({ layers: [] })).toBe(false);
  });
});

describe('runVectorPdfExport → 混合路由（G26）', () => {
  beforeEach(() => {
    mixedCalls.length = 0;
    vectorCalls.length = 0;
    mockedMixed.mockClear();
    cursorState.sessionId = 'sess-g26';
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('数值栅格在场 → exportMixedMap（不调 exportVectorPdf）', async () => {
    cursorState.committed = SPEC_WITH_RASTER_STYLE;
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, { title: '混合图' });
    expect(outcome.ok).toBe(true);
    expect(mixedCalls).toHaveLength(1);
    expect(mixedCalls[0]['sessionId']).toBe('sess-g26');
    expect(mixedCalls[0]['format']).toBe('pdf');
    expect(vectorCalls).toHaveLength(0);
    const msg = hud.messages[0] ?? '';
    expect(msg).toContain('混合地图 PDF');
    expect(msg).toContain('嵌入数值栅格 1 层');
    expect(msg).toContain('mixed_map_x.pdf');
    expect(msg).toContain('导出清单');
    expect(hud.exports[0]?.['filename']).toBe('mixed_map_x.pdf');
  });

  it('纯矢量 → 既有 vector-pdf 载荷路径（零变化）', async () => {
    cursorState.committed = SPEC_VECTOR_ONLY;
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, { title: '纯矢量' });
    expect(outcome.ok).toBe(true);
    expect(vectorCalls).toHaveLength(1);
    expect(mixedCalls).toHaveLength(0);
  });

  it('blocking 冲突 → 如实呈现遗漏清单（不伪成功）', async () => {
    cursorState.committed = SPEC_WITH_RASTER_STYLE;
    mockedMixed.mockRejectedValueOnce({
      status: 409,
      body: {
        detail: {
          code: 'export_blocked_incomplete',
          message: 'omitted',
          omitted: [{ layer_id: 'basemap', reason: 'external_tiles_licence_not_embedded' }],
        },
      },
    });
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, { title: '被阻止' });
    expect(outcome.ok).toBe(false);
    expect(outcome.code).toBe('export_blocked_incomplete');
    const msg = hud.messages[0] ?? '';
    expect(msg).toContain('被阻止');
    expect(msg).toContain('basemap');
  });
});
