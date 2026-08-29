import { describe, it, expect, vi, beforeEach } from 'vitest';
import { captureMapCanvas, composeLayout, downloadBlob, getOversampledZoom, discoverLegendData, COLOR_PALETTES } from './exporter';

describe('exporter', () => {
  describe('getOversampledZoom', () => {
    it('returns original zoom at 96 DPI (screen)', () => {
      expect(getOversampledZoom(10, 96)).toBe(10);
    });

    it('boosts zoom by +1 at 192 DPI (2x retina)', () => {
      expect(getOversampledZoom(10, 192)).toBe(11);
    });

    it('boosts zoom by +2 at 300 DPI (high-res print cap)', () => {
      expect(getOversampledZoom(10, 300)).toBe(12);
    });
  });
  describe('captureMapCanvas', () => {
    it('should return a Blob from the map canvas', async () => {
      const mockBlob = new Blob(['test'], { type: 'image/png' });
      const canvasMock = {
        toBlob: vi.fn((cb) => cb(mockBlob))
      };
      const mapMock = {
        getCanvas: vi.fn(() => canvasMock)
      };

      const result = await captureMapCanvas(mapMock as any);
      expect(result).toBe(mockBlob);
      expect(mapMock.getCanvas).toHaveBeenCalled();
      expect(canvasMock.toBlob).toHaveBeenCalled();
    });
  });

  describe('composeLayout', () => {
    let canvas: HTMLCanvasElement;
    let ctx: any;

    beforeEach(() => {
      ctx = {
        createLinearGradient: vi.fn(() => ({
          addColorStop: vi.fn(),
        })),
        fillRect: vi.fn(),
        fillText: vi.fn(),
        strokeRect: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        closePath: vi.fn(),
        fill: vi.fn(),
        stroke: vi.fn(),
        arc: vi.fn(),
        save: vi.fn(),
        restore: vi.fn(),
        translate: vi.fn(),
        rotate: vi.fn(),
        arcTo: vi.fn(),
        measureText: vi.fn(() => ({ width: 100 })),
        _fillStyle: '',
        set fillStyle(val: string) { this._fillStyle = val; },
        get fillStyle() { return this._fillStyle; },
        _font: '',
        set font(val: string) { this._font = val; },
        get font() { return this._font; },
        set strokeStyle(val: string) {},
        set lineWidth(val: number) {},
        set textAlign(val: string) {},
        set shadowColor(val: string) {},
        set shadowBlur(val: number) {},
      };

      canvas = {
        width: 1000,
        height: 800,
        getContext: vi.fn(() => ctx),
      } as any;
    });

    it('should draw layout elements on the canvas', () => {
      const options = {
        showScale: true,
        showCompass: true,
        showWatermark: true,
        theme: 'light' as const,
        mapCenter: { lat: 0, lng: 0 },
        mapZoom: 10,
        mapBearing: 0,
        dpi: 96,
      };

      composeLayout(canvas, 'Test Title', 'Test Subtitle', options);

      expect(canvas.getContext).toHaveBeenCalledWith('2d');
      expect(ctx.fillText).toHaveBeenCalledWith('Test Title', expect.any(Number), expect.any(Number));
      expect(ctx.fillText).toHaveBeenCalledWith('Test Subtitle', expect.any(Number), expect.any(Number));
    });

    it('should draw legend when thematicLayer is provided as a ThematicStyleDef', () => {
      const options = {
        showLegend: true,
        thematicLayer: {
          type: 'choropleth',
          field: 'population',
          colors: ['#000', '#fff'],
          legend_labels: ['0 - 100', '100 - 200']
        },
        dpi: 96,
      };

      composeLayout(canvas, 'Title', undefined, options);

      expect(ctx.fillText).toHaveBeenCalledWith(expect.stringContaining('population'), expect.any(Number), expect.any(Number));
      expect(ctx.fillText).toHaveBeenCalledWith('0 - 100', expect.any(Number), expect.any(Number));
      expect(ctx.fillText).toHaveBeenCalledWith('100 - 200', expect.any(Number), expect.any(Number));
    });

    it('should draw heatmap gradient legend', () => {
      const options = {
        showLegend: true,
        heatmapLegend: { name: 'Density Heatmap' },
        dpi: 96,
      };

      composeLayout(canvas, 'Title', undefined, options);

      // Should draw the layer name
      expect(ctx.fillText).toHaveBeenCalledWith('DENSITY HEATMAP', expect.any(Number), expect.any(Number));
      // Should draw gradient labels
      expect(ctx.fillText).toHaveBeenCalledWith('极低', expect.any(Number), expect.any(Number));
      expect(ctx.fillText).toHaveBeenCalledWith('极高', expect.any(Number), expect.any(Number));
    });

    it('should draw multiple legends when legendSpec and heatmapLegend are both provided', () => {
      const options = {
        showLegend: true,
        legendSpec: {
          type: 'graduated' as const,
          field: 'income',
          breaks: [1000, 5000, 10000],
          palette: 'YlOrRd',
          palette_colors: ['#ffffb2', '#fed976', '#fd8d3c'],
        },
        heatmapLegend: { name: 'Crime Density' },
        dpi: 96,
      };

      composeLayout(canvas, 'Title', undefined, options);

      // Should draw legend spec field name
      expect(ctx.fillText).toHaveBeenCalledWith(expect.stringContaining('income'), expect.any(Number), expect.any(Number));
      // Should draw heatmap name
      expect(ctx.fillText).toHaveBeenCalledWith('CRIME DENSITY', expect.any(Number), expect.any(Number));
    });
  });

  describe('downloadBlob', () => {
    it('should trigger a download', () => {
      const blob = new Blob(['test'], { type: 'image/png' });
      const filename = 'test.png';
      
      // Mock URL.createObjectURL and URL.revokeObjectURL
      const createObjectURL = vi.fn(() => 'blob:url');
      const revokeObjectURL = vi.fn();
      global.URL.createObjectURL = createObjectURL;
      global.URL.revokeObjectURL = revokeObjectURL;

      // Mock document.createElement and document.body.appendChild/removeChild
      const linkMock = {
        href: '',
        download: '',
        click: vi.fn(),
        style: {}
      };
      const createElement = vi.fn(() => linkMock);
      const appendChild = vi.fn();
      const removeChild = vi.fn();
      if (typeof globalThis.document === 'undefined') {
        (globalThis as any).document = { body: {} };
      }
      globalThis.document.createElement = createElement as any;
      globalThis.document.body.appendChild = appendChild as any;
      globalThis.document.body.removeChild = removeChild as any;

      downloadBlob(blob, filename);

      expect(createObjectURL).toHaveBeenCalledWith(blob);
      expect(createElement).toHaveBeenCalledWith('a');
      expect(linkMock.download).toBe(filename);
      expect(linkMock.click).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:url');
    });
  });
});

// ── discoverLegendData：热力图例去重 + palette 同源 ─────────────────
describe('discoverLegendData — heatmap legend dedup & palette source', () => {
  const heatLayer = {
    visible: true, type: 'heatmap', name: '学校热力',
    legend_spec: { type: 'continuous', min: 0, max: 1, palette_colors: ['#428cd2', '#eb2828'] },
  };

  it('heatmap layer feeds heatmapLegend (with palette_colors), not legendSpec', () => {
    const data = discoverLegendData([heatLayer]);
    expect(data.legendSpec).toBeUndefined();
    // ADR-0081：量化口径（min/max/unit）随色带携带 —— 导出色条与 live
    // FloatingLegend 同源，不再退化为定性 低/高 标签。
    expect(data.heatmapLegend).toEqual({
      name: '学校热力', paletteColors: ['#428cd2', '#eb2828'], min: 0, max: 1,
    });
  });

  it('non-heatmap legend layer wins legendSpec; heatmap still feeds its gradient', () => {
    const choroLayer = {
      visible: true, type: 'vector', name: '区县统计',
      legend_spec: { type: 'graduated', entries: [{ color: '#ffffb2', label: '0-10' }] },
    };
    const data = discoverLegendData([heatLayer, choroLayer]);
    expect(data.legendSpec?.type).toBe('graduated');
    expect(data.heatmapLegend?.paletteColors?.[0]).toBe('#428cd2');
  });
});

// ── COLOR_PALETTES：模型库扩充后的前后端镜像 ─────────────────────────
describe('COLOR_PALETTES — model-library expansion mirror', () => {
  it('carries the ColorBrewer / perceptual-uniform additions', () => {
    for (const pid of ['Oranges','Purples','RdYlGn','RdBu','Set1','Set2','Dark2','Pastel1','Inferno','Plasma']) {
      expect(COLOR_PALETTES[pid], `missing palette ${pid}`).toBeTruthy();
      expect(COLOR_PALETTES[pid]!.length).toBeGreaterThanOrEqual(5);
    }
  });

  it('keeps authoritative hex heads in sync with backend palettes.py', () => {
    expect(COLOR_PALETTES.Set1![0]).toBe('#e41a1c');
    expect(COLOR_PALETTES.RdBu![0]).toBe('#ca0020');
    expect(COLOR_PALETTES.Oranges![0]).toBe('#feedde');
    expect(COLOR_PALETTES.Plasma![0]).toBe('#0d0887');
  });
});

// ── #802: 比例尺长度按真实画布设备像素比换算 ─────────────────────────────

describe('composeLayout scale bar DPR-awareness (#802)', () => {
  function makeCtx() {
    return {
      createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
      fillRect: vi.fn(), fillText: vi.fn(), strokeRect: vi.fn(),
      beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(),
      fill: vi.fn(), stroke: vi.fn(), arc: vi.fn(), save: vi.fn(),
      restore: vi.fn(), translate: vi.fn(), rotate: vi.fn(), arcTo: vi.fn(),
      measureText: vi.fn(() => ({ width: 100 })),
      fillStyle: '', font: '', lineWidth: 0, strokeStyle: '', shadowBlur: 0,
      globalAlpha: 1,
    };
  }
  function barWidthPx(ctx: any): number {
    // strokeRect(bx, by, barPx, bh) —— 比例尺条是导出布局里唯一调用
    // strokeRect 的 1.5 线宽矩形；取其宽度。
    const calls = ctx.strokeRect.mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    return calls[calls.length - 1][2] as number;
  }

  it('等比关系：barPx = nice / metersPerPx × pixelsPerLogicalPx（dpr=2）', () => {
    const ctx = makeCtx();
    const canvas = { width: 2400, height: 1600, getContext: vi.fn(() => ctx) } as any;
    const mapZoom = 10, lat = 0;
    const metersPerPx = (40075016.686 * Math.cos(0)) / (512 * Math.pow(2, mapZoom));
    composeLayout(canvas, 'T', undefined, {
      showScale: true, theme: 'light', mapCenter: { lat, lng: 0 }, mapZoom,
      dpi: 96, pixelsPerLogicalPx: 2,
    });
    const barPx = barWidthPx(ctx);
    const labelMeters = (barPx / 2) * metersPerPx; // 反推标签米数
    // barPx 必须等于某个 nice 值 / mpp × 2（容差容忍 nice 取整）
    const niceCandidates = [1, 2, 5, 10].flatMap((n) =>
      [0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000].map((m) => n * m));
    const matched = niceCandidates.some(
      (nice) => Math.abs(barPx - (nice / metersPerPx) * 2) < 0.5);
    expect(matched).toBe(true);
    // 标签米数在逻辑（CSS）像素语义下成立 —— 不再随 dpr 虚增
    expect(labelMeters).toBeGreaterThan(0);
  });

  it('缺省 pixelsPerLogicalPx 回退 dpi/96（旧调用方语义不变）', () => {
    const ctxA = makeCtx(), ctxB = makeCtx();
    const opts = {
      showScale: true, theme: 'light' as const,
      mapCenter: { lat: 0, lng: 0 }, mapZoom: 10,
    };
    composeLayout({ width: 1000, height: 800, getContext: vi.fn(() => ctxA) } as any,
      'T', undefined, { ...opts, dpi: 96 });
    composeLayout({ width: 1000, height: 800, getContext: vi.fn(() => ctxB) } as any,
      'T', undefined, { ...opts, dpi: 96, pixelsPerLogicalPx: 1 });
    expect(barWidthPx(ctxA)).toBeCloseTo(barWidthPx(ctxB), 6);
  });
});

// ── #803: PDF 帧内等比适配 ───────────────────────────────────────────────

describe('exportToPDF aspect preservation (#803)', () => {
  it('addImage 接收的宽高比与画布一致（A4 横版 1.414 画布不再被拉到 1.63 帧）', async () => {
    vi.resetModules();
    const addImage = vi.fn();
    const rect = vi.fn();
    class FakeJsPDF {
      internal = { pageSize: { getWidth: () => 297, getHeight: () => 210 } };
      addImage = addImage; rect = rect; setDrawColor = vi.fn(); setLineWidth = vi.fn();
      setFontSize = vi.fn(); setTextColor = vi.fn(); text = vi.fn();
      addFont = vi.fn(); setFont = vi.fn(); save = vi.fn(); output = vi.fn(() => 'blob');
      setProperties = vi.fn();
    }
    vi.doMock('jspdf', () => ({ default: FakeJsPDF }));
    try {
      const { exportToPDF } = await import('./exporter');
      const canvas = {
        width: 1414, height: 1000,
        toDataURL: vi.fn(() => 'data:image/png;base64,x'),
      } as any;
      await exportToPDF(canvas, 'T');
      expect(addImage).toHaveBeenCalledTimes(1);
      const [, , , , w, h] = addImage.mock.calls[0];
      expect(w / h).toBeCloseTo(1.414, 2);
      // 居中且不超过帧
      expect(w).toBeLessThanOrEqual(277 + 1e-6);
      expect(h).toBeLessThanOrEqual(170 + 1e-6);
    } finally {
      vi.doUnmock('jspdf');
    }
  });
});
