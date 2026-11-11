/**
 * G25 帧有效文档前端解析器测试 —— 与后端 resolver.py 语义逐位对齐：
 * layerOverrides 覆写、where 过滤、time_window 窗、共享分级不重算、
 * typed 失败（绑定悬空不静默回退）。
 */
import { describe, expect, it } from 'vitest';

import {
  FrameEffectiveSpecError,
  applyFrameLayerOverrides,
  resolveFrameEffectiveSpec,
} from '@/lib/cartography/frame-effective-spec';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

function spec(over: Record<string, unknown> = {}): MapSpec {
  return {
    view: { center: [104, 30.6], zoom: 9 },
    sources: {
      'src-0': {
        type: 'geojson',
        inlineData: {
          type: 'FeatureCollection',
          features: [
            { type: 'Feature', properties: { year: 2020, per_10k: 12 }, geometry: null },
            { type: 'Feature', properties: { year: 2022, per_10k: 14 }, geometry: null },
            { type: 'Feature', properties: { year: 2024, per_10k: 15 }, geometry: null },
          ],
        },
        content_revision: 7,
      },
    },
    layers: [
      {
        id: 'layer-0',
        type: 'fill',
        source: 'src-0',
        legend_spec: {
          type: 'graduated', field: 'per_10k', breaks: [0, 10, 20],
          method: 'equal_interval', k: 2, palette: 'Blues',
        },
      },
    ],
    frame_product: {
      enabled: true,
      product_kind: 'time_series',
      grid: { columns: 3 },
      frames: [
        {
          frame_id: 'f-2022',
          data: {
            ref: 'ref:chengdu:1',
            layer_id: 'layer-0',
            field: 'per_10k',
            content_revision: 7,
            where: { op: 'eq', field: 'year', value: 2022 },
          },
        },
      ],
    },
    layout: {
      frames: [
        {
          id: 'f-2022',
          layerOverrides: {
            'layer-0': { opacity: 0.6 },
          },
        },
      ],
    },
    ...over,
  } as unknown as MapSpec;
}

describe('resolveFrameEffectiveSpec', () => {
  it('where 过滤绑定层要素；layerOverrides 覆写；步骤按序记录', () => {
    const s = spec();
    const out = resolveFrameEffectiveSpec(s, s.frame_product, 'f-2022');
    expect(out.applied).toEqual(['layer_overrides', 'where_filter']);
    expect(out.filteredFeatureCount).toBe(1);
    const layer = (out.document.layers as Array<Record<string, unknown>>)[0];
    expect(layer.opacity).toBe(0.6); // override 生效
    const src = (out.document.sources as Record<string, Record<string, unknown>>)['src-0'];
    const payload = (src.inlineData ?? src.data) as { features: Array<{ properties: { year: number } }> };
    expect(payload.features.map((f) => f.properties.year)).toEqual([2022]);
    // 输入不可变（原 spec 三条要素仍在）。
    const origSrc = (s.sources as Record<string, Record<string, unknown>>)['src-0'];
    expect(((origSrc.inlineData ?? origSrc.data) as { features: unknown[] }).features).toHaveLength(3);
  });

  it('shared_expression：breaks 原样搬运到 legend_spec（同值同色，不重算）', () => {
    const s = spec();
    s.frame_product!.shared_expression = {
      field: 'per_10k', method: 'quantile', k: 4, palette: 'Viridis',
      breaks: [0, 5, 10, 15, 20], domain_status: 'complete',
    };
    const out = resolveFrameEffectiveSpec(s, s.frame_product, 'f-2022');
    expect(out.applied).toContain('shared_expression');
    const layer = (out.document.layers as Array<Record<string, { legend_spec?: Record<string, unknown> }>>)[0];
    const legend = layer.legend_spec!;
    expect(legend.breaks).toEqual([0, 5, 10, 15, 20]); // 域级 breaks 原样（A18）
    expect(legend.method).toBe('quantile');
    expect(legend.k).toBe(4);
    expect(legend.palette).toBe('Viridis');
    expect(legend.shared_scope).toBe(true);
  });

  it('time_window [start,end) 窗过滤（数值域左闭右开）', () => {
    const s = spec();
    const slot = s.frame_product!.frames[0] as { data: Record<string, unknown> };
    delete slot.data.where;
    slot.data.time_window = { time_field: 'year', start: 2020, end: 2024 };
    const out = resolveFrameEffectiveSpec(s, s.frame_product, 'f-2022');
    expect(out.applied).toEqual(['layer_overrides', 'time_window']);
    const src = (out.document.sources as Record<string, Record<string, unknown>>)['src-0'];
    const payload = (src.inlineData ?? src.data) as { features: Array<{ properties: { year: number } }> };
    expect(payload.features.map((f) => f.properties.year)).toEqual([2020, 2022]); // [2020,2024)
  });

  it('time_window 契约 instant → year_int 归一（G15 J16 夹具同形：字符串界）', () => {
    const s = spec();
    const slot = s.frame_product!.frames[0] as { data: Record<string, unknown> };
    delete slot.data.where;
    slot.data.time_window = {
      time_field: 'year', time_representation: 'year_int', granularity: 'year',
      timezone: 'UTC', layer_id: 'layer-0',
      start: '2022-01-01T00:00:00Z', end: '2023-01-01T00:00:00Z',
    };
    const out = resolveFrameEffectiveSpec(s, s.frame_product, 'f-2022');
    expect(out.filteredFeatureCount).toBe(1); // 仅 year=2022 命中
    const src = (out.document.sources as Record<string, Record<string, unknown>>)['src-0'];
    const payload = (src.inlineData ?? src.data) as { features: Array<{ properties: { year: number } }> };
    expect(payload.features[0].properties.year).toBe(2022);
  });

  it('normalizeBoundaryValue：year_int/date_string/epoch_s + 契约时区口径', async () => {
    const { normalizeBoundaryValue } = await import('@/lib/cartography/frame-effective-spec');
    expect(normalizeBoundaryValue('2020-01-01T00:00:00Z', 'year_int')).toBe(2020);
    // 契约时区 Asia/Shanghai：UTC 2021-12-31T18:00 已是上海 2022-01-01。
    expect(normalizeBoundaryValue('2021-12-31T18:00:00Z', 'year_int', '', 'Asia/Shanghai')).toBe(2022);
    expect(normalizeBoundaryValue('2023-05-02T00:00:00Z', 'date_string', 'month')).toBe('2023-05');
    expect(normalizeBoundaryValue('2023-05-02T00:00:00Z', 'date_string', 'year')).toBe('2023');
    expect(normalizeBoundaryValue('2023-05-02T00:00:00Z', 'epoch_s')).toBe(
      Math.floor(Date.parse('2023-05-02T00:00:00Z') / 1000),
    );
    expect(() => normalizeBoundaryValue('not-a-date', 'year_int')).toThrow(FrameEffectiveSpecError);
  });

  it('绑定层缺席 / 源非内联 → typed error（不静默回退全幅）', () => {
    const s = spec();
    (s.frame_product!.frames[0] as { data: { layer_id: string } }).data.layer_id = 'layer-x';
    expect(() => resolveFrameEffectiveSpec(s, s.frame_product, 'f-2022')).toThrow(FrameEffectiveSpecError);

    const s2 = spec();
    const layers = s2.layers as Array<Record<string, unknown>>;
    layers[0].source = 'src-remote';
    (s2.sources as Record<string, unknown>)['src-remote'] = { type: 'geojson', url: 'http://x' };
    try {
      resolveFrameEffectiveSpec(s2, s2.frame_product, 'f-2022');
      expect.unreachable('expected frame_source_not_inline');
    } catch (e) {
      expect(e).toBeInstanceOf(FrameEffectiveSpecError);
      expect((e as FrameEffectiveSpecError).code).toBe('frame_source_not_inline');
    }
  });

  it('帧缺席 → typed error；applyFrameLayerOverrides 未提及键保留', () => {
    const s = spec();
    expect(() => resolveFrameEffectiveSpec(s, s.frame_product, 'f-none')).toThrow(FrameEffectiveSpecError);
    const doc = applyFrameLayerOverrides(spec(), null);
    expect((doc.layers as Array<Record<string, unknown>>)[0].id).toBe('layer-0');
  });
});
