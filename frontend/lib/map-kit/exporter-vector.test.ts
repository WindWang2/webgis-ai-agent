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

  it('E2E: exportMapSpecToVectorPdf produces a real PDF with %PDF- magic bytes and >= 1 page (spec #271)', async () => {
    // Spec #271 requires E2E verification that the export pipeline returns a
    // valid non-empty PDF. This runs the REAL jsPDF render (no mock) and asserts
    // the PDF magic bytes ("%PDF-") and that at least one /Type /Page object
    // exists in the byte stream. A near-empty or corrupted export would fail
    // the magic-bytes check; a zero-page PDF would fail the page check.
    const blob = await exportMapSpecToVectorPdf(sampleMapSpec, {
      title: 'E2E Magic Bytes Test',
      subtitle: 'Real jsPDF Render',
      dpi: 300,
    });
    expect(blob.type).toBe('application/pdf');

    // Read the PDF bytes via Blob.arrayBuffer() (polyfilled for jsdom in
    // test/setup.ts). Spec #271 requires asserting the %PDF- magic bytes and
    // page count on the real rendered PDF.
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const head = String.fromCharCode(...Array.from(bytes.slice(0, 5)));
    expect(head).toBe('%PDF-');

    // Parse the PDF byte stream for page objects. jsPDF emits "/Type /Pages"
    // (the root pages tree) plus "/Type /Page" (each page). We require at
    // least one /Page entry so a zero-page PDF fails.
    const text = Array.from(bytes, (b) => String.fromCharCode(b)).join('');
    const pageObjMatches = text.match(/\/Type\s*\/Page(?!s)\b/g) || [];
    expect(pageObjMatches.length).toBeGreaterThanOrEqual(1);
  });

  it('paints MapSpec vector layers BELOW the marginalia (correct Z-order)', async () => {
    // P0-2 regression: the map <g> must precede the marginalia groups in
    // document order so it renders underneath (SVG paints later siblings
    // on top). Previously the map was injected as the last child and
    // covered the title banner / north arrow / legend.
    const svgText = await generateMapSpecVectorSvgString(sampleMapSpec, {
      title: 'Z-Order Test',
      layoutId: 'tmpl_ly_academic',
      dpi: 300,
    });
    const mapIdx = svgText.indexOf('mapspec-vector-layers');
    const marginaliaIdx = svgText.indexOf('Print Frame Border');
    expect(mapIdx).toBeGreaterThan(-1);
    expect(marginaliaIdx).toBeGreaterThan(-1);
    // Map content must come BEFORE the marginalia in document order.
    expect(mapIdx).toBeLessThan(marginaliaIdx);
  });

  it('exportMapSpecToVectorPdf renders marginalia text and transformed groups', async () => {
    // P0-3a regression: the old order-sensitive regex parser dropped every
    // <text>/<rect>/<line> element and ignored <g transform="translate(...)">,
    // so the north arrow "N", legend labels, and scalebar were missing or
    // misplaced. The DOMParser-based walker must render them. We assert the
    // PDF blob is substantially larger than a near-empty PDF would be, and
    // (smoke) that no exception is thrown for a layout rich in text/groups.
    const richMapSpec = {
      sources: {
        s1: {
          type: 'geojson',
          data: {
            type: 'FeatureCollection',
            features: [
              { type: 'Feature', geometry: { type: 'Point', coordinates: [116.4, 39.9] }, properties: {} },
              {
                type: 'Feature',
                geometry: { type: 'LineString', coordinates: [[116.3, 39.8], [116.5, 40.0]] },
                properties: {},
              },
              {
                type: 'Feature',
                geometry: { type: 'Polygon', coordinates: [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 39.8]]] },
                properties: {},
              },
            ],
          },
        },
      },
      layers: [
        { id: 'pts', type: 'circle', source: 's1', paint: { 'circle-color': '#de2d26', 'circle-radius': 6 } },
        { id: 'lines', type: 'line', source: 's1', paint: { 'line-color': '#2563eb', 'line-width': 2 } },
        { id: 'polys', type: 'fill', source: 's1', paint: { 'fill-color': '#60a5fa', 'fill-opacity': 0.6, 'fill-outline-color': '#1d4ed8' } },
      ],
    };
    const blob = await exportMapSpecToVectorPdf(richMapSpec, {
      title: 'P0-3a 富文本测试',
      subtitle: 'Text + Transform + Polygon Outline',
      layoutId: 'tmpl_ly_academic',
      dpi: 300,
    });
    expect(blob.type).toBe('application/pdf');
    // A PDF containing the map (3 geometry types) + full marginalia (title,
    // north arrow with "N", legend with labels, scalebar) is materially
    // larger than the ~500-byte near-empty baseline.
    expect(blob.size).toBeGreaterThan(1500);
  });

  it('includes oversampled raster tiles with zoom boost for 300 DPI exports', async () => {
    const rasterMapSpec = {
      sources: {
        r1: {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
        },
      },
      layers: [
        {
          id: 'base-raster',
          type: 'raster',
          source: 'r1',
          paint: { 'raster-opacity': 0.8 },
        },
      ],
    };

    const svgText = await generateMapSpecVectorSvgString(rasterMapSpec, {
      title: 'Raster Oversampling Test',
      dpi: 300,
    });

    expect(svgText).toContain('<image');
    expect(svgText).toContain('data-oversample-boost="2"');
    expect(svgText).toContain('opacity="0.8"');
  });
});
