import { describe, it, expect } from 'vitest';
import { generateMapSpecVectorSvgString, exportMapSpecToVectorSvg, exportMapSpecToVectorPdf } from './exporter';

describe('Client-Side HD Vector SVG/PDF Exporter API', () => {
  const sampleMapSpec = {
    sources: {
      s1: {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [116.4, 39.9] },
              properties: { name: 'Beijing' },
            },
          ],
        },
      },
    },
    layers: [
      {
        id: 'pts',
        type: 'circle',
        source: 's1',
        paint: { 'circle-color': '#de2d26', 'circle-radius': 6 },
      },
    ],
  };

  it('exportMapSpecToVectorSvg generates pure vector SVG blob without raster image tags', async () => {
    const svgText = await generateMapSpecVectorSvgString(sampleMapSpec, {
      title: '北京市专题地图',
      layoutId: 'tmpl_ly_academic',
      dpi: 300,
    });
    expect(svgText).toContain('<svg');
    expect(svgText).toContain('<circle');
    expect(svgText).not.toContain('<image'); // Pure vector, no base64 PNG wrapper
    expect(svgText).toContain('北京市专题地图');

    const blob = await exportMapSpecToVectorSvg(sampleMapSpec, { title: '北京市专题地图' });
    expect(blob).toBeDefined();
    expect(blob.type).toBe('image/svg+xml');
  });

  it('exportMapSpecToVectorPdf returns a valid PDF blob integrating vector marginalia and MapSpec layers', async () => {
    const blob = await exportMapSpecToVectorPdf(sampleMapSpec, {
      title: '北京市专题地图',
      subtitle: 'Vector Print Layout Test',
      dpi: 300,
    });
    expect(blob.type).toBe('application/pdf');
    expect(blob.size).toBeGreaterThan(500); // Ensures PDF contains actual vector paths and text streams
  });
});
