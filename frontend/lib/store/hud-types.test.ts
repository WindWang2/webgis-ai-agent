import { describe, it, expect } from 'vitest';
import type { ExportItem } from './hud-types';

describe('ExportItem type', () => {
  it('allows svg type', () => {
    const item: ExportItem = {
      id: '1',
      name: 'test',
      type: 'svg',
      size: '100KB',
      date: '2026-05-31',
    };
    expect(item.type).toBe('svg');
  });

  it('allows filename field', () => {
    const item: ExportItem = {
      id: '1',
      name: 'test',
      type: 'png',
      size: '100KB',
      date: '2026-05-31',
      filename: 'map_export_123.png',
    };
    expect(item.filename).toBe('map_export_123.png');
  });

  it('supports all export types', () => {
    const types: ExportItem['type'][] = ['png', 'pdf', 'svg', 'geojson'];
    expect(types).toHaveLength(4);
  });
});
