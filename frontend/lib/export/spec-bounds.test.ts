import { describe, expect, it } from 'vitest';
import { inferSpecDataBounds } from './spec-bounds';

describe('inferSpecDataBounds', () => {
  it('递归提取 GeometryCollection 内（含嵌套集合）的坐标范围', () => {
    const spec = {
      sources: {
        mixed: {
          inlineData: {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                properties: {},
                geometry: {
                  type: 'GeometryCollection',
                  geometries: [
                    { type: 'Point', coordinates: [120, 30] },
                    { type: 'LineString', coordinates: [[116, 39], [121, 40]] },
                    {
                      type: 'GeometryCollection',
                      geometries: [{ type: 'Point', coordinates: [115, 41] }],
                    },
                  ],
                },
              },
            ],
          },
        },
      },
    };

    expect(inferSpecDataBounds(spec)).toEqual([115, 30, 121, 41]);
  });
});
