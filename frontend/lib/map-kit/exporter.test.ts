import { describe, it, expect, vi, beforeEach } from 'vitest';
import { captureMapCanvas, composeLayout, downloadBlob } from './exporter';

describe('exporter', () => {
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
        theme: 'light',
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
      global.document.createElement = createElement as any;
      global.document.body.appendChild = appendChild as any;
      global.document.body.removeChild = removeChild as any;

      downloadBlob(blob, filename);

      expect(createObjectURL).toHaveBeenCalledWith(blob);
      expect(createElement).toHaveBeenCalledWith('a');
      expect(linkMock.download).toBe(filename);
      expect(linkMock.click).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:url');
    });
  });
});
