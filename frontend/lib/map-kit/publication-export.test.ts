import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// —— 桩面：session-cursor / exporter 合成器 / ref 注入 / API 客户端 ————
const cursorState = {
  committed: { version: '1.1', layers: [] } as Record<string, unknown> | null,
  sessionId: 'sess-42' as string | undefined,
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
    composed: true,
  })),
  MapExporterEngine: {
    export: vi.fn(async () => ({ ok: true, format: 'pdf' })),
  },
}));

vi.mock('@/lib/mapspec/ref-source-resolver', () => ({
  injectResolvedRefSources: vi.fn(
    (spec: Record<string, unknown>) => ({ ...spec, inlined: true }) as never,
  ),
}));

vi.mock('@/lib/api/publication', () => ({
  exportVectorPdf: vi.fn(),
  vectorPdfErrorCode: vi.fn(
    (err: { code?: string | null }) => err.code ?? null,
  ),
}));

import { exportVectorPdf } from '@/lib/api/publication';
import { runVectorPdfExport, atlasPolicyFromRequest } from './publication-export';

const mockedExport = vi.mocked(exportVectorPdf);

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

describe('runVectorPdfExport（C14 前端入口）', () => {
  beforeEach(() => {
    mockedExport.mockReset();
    cursorState.committed = { version: '1.1', layers: [] };
    cursorState.sessionId = 'sess-42';
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('成功：composed+内联 spec 提交，回执与系统消息落盘', async () => {
    mockedExport.mockResolvedValue({
      success: true,
      filename: 'map_vector_1.pdf',
      url: '/api/v1/export/download/map_vector_1.pdf',
      format: 'pdf',
      vector: true,
      pages: 3,
      frames_rendered: 3,
      frames_skipped: 0,
      layout_version: '1.0.0',
      atlas: true,
      atlas_pages: [{ page_id: 'cover' }, { page_id: 'a' }, { page_id: 'b' }],
      render_diagnostics: [{ code: 'raster_layer_unavailable_vector_pdf', detail: 's1' }],
    });
    const hud = makeHud();
    const outcome = await runVectorPdfExport(
      () => hud,
      {},
      { title: 'T', atlas: { driver: 'category', categoryProperty: 'zone', includeCover: true } },
    );
    expect(outcome.ok).toBe(true);
    const body = mockedExport.mock.calls[0][0];
    expect(body.mapspec).toMatchObject({ composed: true, inlined: true });
    expect(body.sessionId).toBe('sess-42');
    expect(body.atlas).toMatchObject({ driver: 'category', categoryProperty: 'zone', includeCover: true });
    expect(hud.exports[0]).toMatchObject({ filename: 'map_vector_1.pdf', type: 'pdf' });
    expect(hud.messages[0]).toContain('矢量 PDF');
    expect(hud.messages[0]).toContain('raster_layer_unavailable_vector_pdf');
  });

  it('无 committed spec → 诚实失败 no_mapspec，不发请求', async () => {
    cursorState.committed = null;
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, {});
    expect(outcome.ok).toBe(false);
    expect(outcome.error).toBe('no_mapspec');
    expect(mockedExport).not.toHaveBeenCalled();
  });

  it('503 vector_pdf_unavailable → 回退栅格 PDF 并披露', async () => {
    mockedExport.mockRejectedValue({ code: 'vector_pdf_unavailable' });
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, { title: 'T' });
    expect(outcome.ok).toBe(true);
    expect(outcome.fellBackToRaster).toBe(true);
    expect(hud.messages[0]).toContain('vector_pdf_unavailable');
    expect(hud.messages[0]).toContain('回退栅格');
  });

  it('429 busy → 结构化 busy 提示，不回退', async () => {
    mockedExport.mockRejectedValue({ code: 'vector_pdf_busy' });
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, { title: 'T' });
    expect(outcome.ok).toBe(false);
    expect(outcome.error).toBe('vector_pdf_busy');
    expect(hud.messages[0]).toContain('占用中');
  });

  it('400 typed code（ref 未内联）→ 透传 + 指引', async () => {
    mockedExport.mockRejectedValue({ code: 'mapspec_ref_sources_unhydrated' });
    const hud = makeHud();
    const outcome = await runVectorPdfExport(() => hud, {}, { title: 'T' });
    expect(outcome.ok).toBe(false);
    expect(outcome.code).toBe('mapspec_ref_sources_unhydrated');
    expect(hud.messages.join('\n')).toContain('ref 载体源');
  });
});

describe('atlasPolicyFromRequest', () => {
  it('undefined 透传；空串字段不产出键', () => {
    expect(atlasPolicyFromRequest(undefined)).toBeUndefined();
    expect(atlasPolicyFromRequest({ driver: 'frames' })).toEqual({
      driver: 'frames',
      includeCover: false,
    });
  });
});
