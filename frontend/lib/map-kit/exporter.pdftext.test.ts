/**
 * ac-08（ADR-0157 P3/P5）：PDF 文本层与出版档集成契约。
 *
 * 1. 出版字体可用（fontB64 注入 + jsPDF mock 持 VFS API）→ CJK 标题走
 *    doc.text 真文本层 + `pdf_cjk_font_embedded` 披露（栅格化不再是默认）；
 * 2. 字体不可用 → 维持 W6 语义：CJK 随画布栅格化 + `pdf_text_rasterized_cjk`
 *   （最后兜底）；ASCII 标题不受影响（真矢量）；
 * 3. 出版档（color_mode=cmyk）→ exportToPDF 收到 colorMode；出血页面尺寸 =
 *    trim + 2×3mm；栅格件近似披露 cmyk_approximate_raster。
 */
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import {
  runExport,
  exportToPDF,
  MapExporterEngine,
  type ExportDeps,
  type ExportRequest,
} from './exporter';
import { __setPublicationFontCacheForTests } from '../export/pdf-font';

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8001',
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

interface JsPdfFake {
  internal: { pageSize: { getWidth: () => number; getHeight: () => number } };
  addImage: ReturnType<typeof vi.fn>;
  setDrawColor: ReturnType<typeof vi.fn>;
  setLineWidth: ReturnType<typeof vi.fn>;
  rect: ReturnType<typeof vi.fn>;
  setFontSize: ReturnType<typeof vi.fn>;
  setTextColor: ReturnType<typeof vi.fn>;
  text: ReturnType<typeof vi.fn>;
  setProperties: ReturnType<typeof vi.fn>;
  output: ReturnType<typeof vi.fn>;
  addFileToVFS?: ReturnType<typeof vi.fn>;
  addFont?: ReturnType<typeof vi.fn>;
  setFont?: ReturnType<typeof vi.fn>;
  line?: ReturnType<typeof vi.fn>;
}

/** 可配置 VFS 能力的 jsPDF 假体工厂。 */
function makeJsPdfMock(opts: { withFontApi?: boolean; pageW?: number; pageH?: number } = {}) {
  const calls: { text: string[]; lines: Array<[number, number, number, number]> } = {
    text: [],
    lines: [],
  };
  const fake: JsPdfFake = {
    internal: { pageSize: { getWidth: () => opts.pageW ?? 297, getHeight: () => opts.pageH ?? 210 } },
    addImage: vi.fn(),
    setDrawColor: vi.fn(),
    setLineWidth: vi.fn(),
    rect: vi.fn(),
    setFontSize: vi.fn(),
    setTextColor: vi.fn(),
    text: vi.fn((s: string) => calls.text.push(String(s))),
    setProperties: vi.fn(),
    output: vi.fn(() => new Blob(['pdf-data'], { type: 'application/pdf' })),
    line: vi.fn((x1: number, y1: number, x2: number, y2: number) => calls.lines.push([x1, y1, x2, y2])),
  };
  if (opts.withFontApi) {
    fake.addFileToVFS = vi.fn();
    fake.addFont = vi.fn();
    fake.setFont = vi.fn();
  }
  return { fake, calls };
}

let currentFake: JsPdfFake;

vi.mock('jspdf', () => ({
  default: class {
    constructor() {
      return currentFake as unknown as object;
    }
  },
}));

beforeEach(() => {
  vi.restoreAllMocks();
  __setPublicationFontCacheForTests(undefined);
});

afterEach(() => {
  __setPublicationFontCacheForTests(undefined);
  vi.unstubAllGlobals();
});

function stubUpload() {
  const mockFetch = vi.fn((reqUrl: string) => {
    if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ url: '/exports/map.pdf', filename: 'map.pdf' }),
      } as any);
    }
    return Promise.resolve({
      ok: true,
      blob: async () => new Blob(['img-data'], { type: 'image/png' }),
    } as any);
  }) as Mock;
  vi.stubGlobal('fetch', mockFetch);
}

function createDeps(): ExportDeps {
  const hudState = {
    theme: 'light' as const,
    layers: [],
    addExport: vi.fn(),
    setPendingSystemMessage: vi.fn(),
  };
  return {
    map: {
      getPixelRatio: vi.fn(() => 1),
      setPixelRatio: vi.fn(),
      getCanvas: vi.fn(() => {
        const canvas = document.createElement('canvas');
        canvas.width = 800;
        canvas.height = 600;
        return canvas;
      }),
      getCenter: vi.fn(() => ({ lat: 39.9, lng: 116.4 })),
      getZoom: vi.fn(() => 10),
      getBearing: vi.fn(() => 0),
      once: vi.fn((_event: string, cb: () => void) => cb()),
    } as any,
    getHudState: () => hudState,
  };
}

describe('PDF 文本层（ADR-0157 P3：CJK 子集嵌入）', () => {
  it('字体可用 → CJK 标题进 doc.text 真文本层 + pdf_cjk_font_embedded 披露', async () => {
    // 模拟字体已加载（base64 注入，绕过网络）；TTF 头 + padding。
    __setPublicationFontCacheForTests(
      btoa(String.fromCharCode(0, 1, 0, 0) + 'fontdata'.padEnd(8, '\0')),
    );
    const { fake, calls } = makeJsPdfMock({ withFontApi: true });
    currentFake = fake;
    stubUpload();
    const deps = createDeps();
    const req: ExportRequest = { title: '成都市学校分布图', format: 'pdf' };

    const outcome = await runExport(deps, req);
    expect(outcome.ok).toBe(true);
    // CJK 标题真文本层（不再栅格化）
    expect(calls.text).toContain('成都市学校分布图');
    // 嵌入披露
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('pdf_cjk_font_embedded');
    expect(finalMsg).toContain('嵌入 Noto Sans SC');
    // 字体注册链
    expect(fake.addFileToVFS).toHaveBeenCalled();
    expect(fake.addFont).toHaveBeenCalled();
  });

  it('字体不可用 → W6 兜底语义保持：CJK 栅格化 + pdf_text_rasterized_cjk', async () => {
    // 缓存为 null（加载失败）—— pdf_text_rasterized_cjk 作为最后兜底。
    __setPublicationFontCacheForTests(null);
    const { fake, calls } = makeJsPdfMock();
    currentFake = fake;
    stubUpload();
    const deps = createDeps();
    const req: ExportRequest = { title: '成都市学校分布图', format: 'pdf' };

    const outcome = await runExport(deps, req);
    expect(outcome.ok).toBe(true);
    // 文本层不含 CJK 标题（栅格化承载）
    expect(calls.text).not.toContain('成都市学校分布图');
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('pdf_text_rasterized_cjk');
  });
});

describe('PDF 出版档（ADR-0157 P5：出血 + 裁切线）', () => {
  it('color_mode=cmyk → 页面 = trim + 2×3mm，裁切线画至页缘，披露 cmyk_approximate_raster', async () => {
    const { fake, calls } = makeJsPdfMock({ pageW: 303, pageH: 216 }); // A4 横版含出血
    currentFake = fake;
    // 直接测 exportToPDF 层（出血几何在此实现）
    const canvas = document.createElement('canvas');
    canvas.width = 800;
    canvas.height = 600;
    const blob = await exportToPDF(canvas, 'T', undefined, {
      colorMode: 'cmyk',
    });
    expect(blob).toBeInstanceOf(Blob);
    // trim = 297×210；裁切角线臂长 3mm 自 trim 角延伸至页缘
    expect(fake.line).toHaveBeenCalled();
    const lines = calls.lines;
    // 左上角水平臂：(3,3) → (0,3)
    expect(lines).toContainEqual([3, 3, 0, 3]);
    // 右下角垂直臂：(300, 213) → (300, 216)
    expect(lines).toContainEqual([300, 213, 300, 216]);
  });

  it('runExport pdf + color_mode=cmyk → 系统消息含 cmyk_approximate_raster 近似披露', async () => {
    const { fake } = makeJsPdfMock();
    currentFake = fake;
    stubUpload();
    const deps = createDeps();
    const req: ExportRequest = { title: '出版图', format: 'pdf', color_mode: 'cmyk' };
    const outcome = await runExport(deps, req);
    expect(outcome.ok).toBe(true);
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('cmyk_approximate_raster');
    expect(finalMsg).toContain('3mm 出血');
  });
});

describe('runExport → exportToPDF 集成通道', () => {
  it('pdf 请求把 colorMode/fontB64 传入 exportToPDF（参数完整性）', async () => {
    const spy = vi.spyOn(MapExporterEngine, 'exportToPDF').mockResolvedValue(
      new Blob(['pdf'], { type: 'application/pdf' }),
    );
    stubUpload();
    const deps = createDeps();
    const req: ExportRequest = { title: 'T', format: 'pdf', color_mode: 'srgb' };
    await runExport(deps, req);
    const opts = spy.mock.calls[0][3] as Record<string, unknown>;
    expect(opts['colorMode']).toBe('srgb');
    // 字体加载失败（jsdom fetch stub 非 TTF）→ fontB64 null 但键在
    expect('fontB64' in opts).toBe(true);
  });
});
