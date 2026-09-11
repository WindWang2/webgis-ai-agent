/**
 * Binary/inline fixtures for journey runs (ADR-0146 determinism: every byte
 * here is fixed, no runtime random generation inside journeys).
 */

/** Minimal non-empty GeoJSON FeatureCollection (3 points around Beijing). */
export const SAMPLE_GEOJSON: unknown = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', properties: { name: 'A', value: 10 }, geometry: { type: 'Point', coordinates: [116.4, 39.9] } },
    { type: 'Feature', properties: { name: 'B', value: 20 }, geometry: { type: 'Point', coordinates: [116.5, 39.95] } },
    { type: 'Feature', properties: { name: 'C', value: 30 }, geometry: { type: 'Point', coordinates: [116.45, 40.02] } },
  ],
};

/**
 * 1×1 transparent PNG (same bytes the visual harness uses for tiles) — stands
 * in for export artifacts in mock mode; journeys assert the magic header and
 * non-zero size, not the pixels.
 */
export const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

/**
 * Minimal valid .zip container (local file header + entries) with the shapefile
 * sidecar names — enough for POST /upload content checks in mock mode (the
 * stub answers before the body is inspected; real-mode upload sends a real
 * zip produced by the backend test fixture scripts instead).
 */
export function fakeShapefileZip(): Buffer {
  // Stored (uncompressed) zip with one empty file entry 'j1.shp'.
  const name = 'j1.shp';
  const nameBytes = Buffer.from(name, 'utf8');
  const crc = 0; // empty file
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(20, 4); // version needed
  local.writeUInt16LE(0, 6); // flags
  local.writeUInt16LE(0, 8); // method: stored
  local.writeUInt16LE(0, 10); // time
  local.writeUInt16LE(0x2186, 12); // date (2000-01-01, fixed)
  local.writeUInt32LE(crc, 14);
  local.writeUInt32LE(0, 18); // compressed size
  local.writeUInt32LE(0, 22); // uncompressed size
  local.writeUInt16LE(nameBytes.length, 26);
  local.writeUInt16LE(0, 28);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0);
  central.writeUInt16LE(20, 4);
  central.writeUInt16LE(20, 6);
  central.writeUInt32LE(crc, 16);
  central.writeUInt32LE(0, 20);
  central.writeUInt32LE(0, 24);
  central.writeUInt16LE(nameBytes.length, 28);
  central.writeUInt32LE(30 + nameBytes.length, 42); // local header offset
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(1, 8);
  end.writeUInt16LE(1, 10);
  end.writeUInt32LE(46 + nameBytes.length, 12);
  end.writeUInt16LE(30, 16);
  return Buffer.concat([local, nameBytes, central, nameBytes, end]);
}

/**
 * Minimal Mapbox Vector Tile stand-in is not needed byte-wise: the frontend
 * requests MVT and hands the buffer to maplibre; journey 6 asserts the *request
 * contract* (URL + 200 + mvt content type) and the layer reaching `loaded`
 * state. A zero-length 200 with `application/vnd.mapbox-vector-tile` exercises
 * exactly that contract path without embedding a tile encoder in fixtures.
 */
export const EMPTY_MVT_HEADERS = { 'content-type': 'application/vnd.mapbox-vector-tile' };
