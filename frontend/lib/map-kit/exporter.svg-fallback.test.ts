/**
 * SVG 位图回退（vector 编译失败双保险）标题一致性：<title> 必须用
 * effTitle（请求标题 > spec title 组件），与 PDF 链同源 —— 此前用请求
 * title，spec-only 标题时退化为 'map'。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8001',
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// 强制矢量编译失败 → 走 exporter 的双保险位图回退。
vi.mock('@/lib/map-kit/vector-svg-export', () => ({
  buildVectorSvgExport: vi.fn(() => {
    throw new Error('force raster fallback');
  }),
}));

import { runExport } from '@/lib/map-kit/exporter';

function createMockCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = 800;
  canvas.height = 600;
  return canvas;
}

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SVG raster fallback title', () => {
  it('uses the spec title component when the request has no title', async () => {
    const cursor = await import('@/lib/mapspec/session-cursor');
    cursor.setMapSpecSessionCursor('svg-fallback-title');
    expect(cursor.commitMapSpecDocument({
      id: 'svg-fallback-title',
      version: '1.1',
      layers: [],
      sources: {},
      view: {},
      layout: {
        components: [
          { id: 'title', type: 'title', enabled: true, options: { text: '规格标题' } },
        ],
      },
    } as never, 1)).toBe(true);

    const uploaded: Blob[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
      const body = init?.body;
      if (body instanceof FormData) {
        const file = body.get('file');
        if (file instanceof Blob) uploaded.push(file);
      }
      return {
        ok: true,
        json: async () => ({ url: '/exports/map.svg', filename: 'map.svg' }),
        blob: async () => new Blob(['ok'], { type: 'image/svg+xml' }),
      } as unknown as Response;
    }));

    const hud = {
      theme: 'light',
      layers: [],
      addExport: vi.fn(),
      setPendingSystemMessage: vi.fn(),
    };
    const deps = {
      map: {
        getPixelRatio: vi.fn(() => 1),
        setPixelRatio: vi.fn(),
        getCanvas: vi.fn(() => createMockCanvas()),
        getCenter: vi.fn(() => ({ lat: 39.9, lng: 116.4 })),
        getZoom: vi.fn(() => 10),
        getBearing: vi.fn(() => 0),
        getPitch: vi.fn(() => 0),
        once: vi.fn((_event: string, cb: () => void) => cb()),
      },
      getHudState: () => hud,
    };

    try {
      const outcome = await runExport(deps as never, { format: 'svg' });
      expect(outcome.ok).toBe(true);
      expect(uploaded).toHaveLength(1);
      const text = await readBlob(uploaded[0]);
      // 位图包装形态（<image> 嵌 PNG）证明确实走了回退分支。
      expect(text).toContain('<image');
      expect(text).toContain('<title>规格标题</title>');
      expect(text).not.toContain('<title>map</title>');
    } finally {
      cursor.resetLiveState();
    }
  });
});
