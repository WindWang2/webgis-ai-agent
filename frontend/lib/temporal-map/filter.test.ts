/**
 * temporal-map 过滤编译器测试（GOAL 03 M4）。
 *
 * 与后端编译器语义镜像断言（同表示归一/同 [start,end)/同 AND 词表）：
 * 地图 layer.filter、图表/表格 where AST、fingerprint 三面同源。
 */
import { describe, expect, it } from 'vitest';
import type { MapSpec, MapSpecTemporalConfig } from '@/lib/mapspec-compiler/types';
import {
  applyTemporalToSpec,
  normalizeBoundaryValue,
  temporalFilterExpression,
  temporalFingerprint,
  temporalWhereAst,
} from './filter';
import { readTemporalView } from './contract';

function config(overrides: Partial<MapSpecTemporalConfig> = {}): MapSpecTemporalConfig {
  return {
    schema_version: 'temporal_view.v1',
    enabled: true,
    layers: [{ layer_id: 'schools', source_ref: 'ref:schools' }],
    time_field: 'year',
    time_semantics: 'snapshot',
    time_representation: 'year_int',
    timezone: 'UTC',
    granularity: 'year',
    extent: ['2021-01-01T00:00:00Z', '2024-01-01T00:00:00Z'],
    frames: [
      { id: 'f-2021', label: '2021', start: '2021-01-01T00:00:00Z', end: '2022-01-01T00:00:00Z', feature_count: 2 },
      { id: 'f-2022', label: '2022', start: '2022-01-01T00:00:00Z', end: '2023-01-01T00:00:00Z', feature_count: 1 },
      { id: 'f-2023', label: '2023', start: '2023-01-01T00:00:00Z', end: '2024-01-01T00:00:00Z', feature_count: 3 },
    ],
    cursor_frame: 'f-2022',
    source_ref: 'ref:schools',
    content_revision: 7,
    ...overrides,
  } as MapSpecTemporalConfig;
}

const F23 = { id: 'f-2023', label: '2023', start: '2023-01-01T00:00:00Z', end: '2024-01-01T00:00:00Z' };

describe('normalizeBoundaryValue（表示归一 —— 与后端镜像）', () => {
  it('year_int → 年份数值', () => {
    expect(normalizeBoundaryValue('2023-01-01T00:00:00Z', 'year_int', 'year')).toBe(2023);
  });
  it('epoch_ms → 毫秒数值', () => {
    expect(normalizeBoundaryValue('2023-01-01T00:00:00Z', 'epoch_ms', 'year')).toBe(1672531200000);
  });
  it('epoch_s → 秒数值', () => {
    expect(normalizeBoundaryValue('2023-01-01T00:00:00Z', 'epoch_s', 'year')).toBe(1672531200);
  });
  it('date_string month → 零填充月串（字典序 = 时间序）', () => {
    expect(normalizeBoundaryValue('2023-04-01T00:00:00Z', 'date_string', 'month')).toBe('2023-04');
  });
  it('iso_string → 原样 ISO（同格式同偏移前提）', () => {
    expect(normalizeBoundaryValue('2023-04-01T00:00:00Z', 'iso_string', 'day'))
      .toBe('2023-04-01T00:00:00Z');
  });
});

describe('三面同源（地图/统计/指纹）', () => {
  it('filter 表达式与 where AST 值一致', () => {
    const cfg = config();
    const expr = temporalFilterExpression(cfg, F23);
    const ast = temporalWhereAst(cfg, F23);
    expect(expr).toEqual(['all',
      ['>=', ['get', 'year'], 2023],
      ['<', ['get', 'year'], 2024]]);
    expect((ast.terms as { value: unknown }[])[0].value).toBe(2023);
    expect((ast.terms as { value: unknown }[])[1].value).toBe(2024);
  });
  it('fingerprint 随 (revision, frame) 变化', () => {
    const cfg = config();
    expect(temporalFingerprint(cfg, 'f-2023')).toBe(temporalFingerprint(cfg, 'f-2023'));
    expect(temporalFingerprint(cfg, 'f-2023')).not.toBe(temporalFingerprint(cfg, 'f-2022'));
    const bumped = config({ content_revision: 8 });
    expect(temporalFingerprint(cfg, 'f-2023')).not.toBe(temporalFingerprint(bumped, 'f-2023'));
  });
});

describe('applyTemporalToSpec（reconcile 注入）', () => {
  const spec: MapSpec = {
    version: '1.8',
    layers: [
      { id: 'schools', type: 'circle', source: 's1' },
      { id: 'other', type: 'circle', source: 's2', filter: ['==', 'kind', 'x'] },
    ],
  } as unknown as MapSpec;

  it('绑定层注入时间过滤（AND 既有 filter）；非绑定层不动', () => {
    const out = applyTemporalToSpec(spec, config(), F23);
    const schools = out.layers!.find((l) => l.id === 'schools')!;
    expect(schools.filter).toEqual(['all',
      ['>=', ['get', 'year'], 2023],
      ['<', ['get', 'year'], 2024]]);
    const other = out.layers!.find((l) => l.id === 'other')!;
    expect(other.filter).toEqual(['==', 'kind', 'x']); // 未触碰
    // 纯函数：输入 spec 不变
    expect(spec.layers!.find((l) => l.id === 'schools')!.filter).toBeUndefined();
  });
  it('绑定层带既有 filter → AND 合成（不覆盖用户过滤）', () => {
    const spec2 = {
      version: '1.8',
      layers: [{ id: 'schools', type: 'circle', source: 's1', filter: ['==', 'kind', 'x'] }],
    } as unknown as MapSpec;
    const out = applyTemporalToSpec(spec2, config(), F23);
    expect(out.layers![0].filter).toEqual(['all',
      ['==', 'kind', 'x'],
      ['all', ['>=', ['get', 'year'], 2023], ['<', ['get', 'year'], 2024]]]);
  });
  it('帧缺省 → 用 spec cursor 帧', () => {
    const out = applyTemporalToSpec(spec, config());
    const schools = out.layers!.find((l) => l.id === 'schools')!;
    expect(schools.filter).toEqual(['all',
      ['>=', ['get', 'year'], 2022],
      ['<', ['get', 'year'], 2023]]);
  });
  it('绑定层不在 spec.layers → 原样返回（不伪造）', () => {
    const cfg = config({ layers: [{ layer_id: 'ghost' }] });
    expect(applyTemporalToSpec(spec, cfg)).toBe(spec);
  });
});

describe('readTemporalView（契约读取）', () => {
  it('无 temporal / 未启用 → null', () => {
    expect(readTemporalView(null)).toBeNull();
    expect(readTemporalView({ version: '1.8' } as MapSpec)).toBeNull();
    expect(readTemporalView({ version: '1.8', temporal: config({ enabled: false }) } as unknown as MapSpec)).toBeNull();
  });
  it('缺期旗标透出 missingHint', () => {
    const cfg = config({ quality: ['missing_period_gap'] });
    const view = readTemporalView({ version: '1.8', temporal: cfg } as unknown as MapSpec);
    expect(view?.missingHint).toBe(true);
  });
  it('坏游标 → 回退首帧（不抛）', () => {
    const cfg = config({ cursor_frame: 'f-2099' });
    const view = readTemporalView({ version: '1.8', temporal: cfg } as unknown as MapSpec);
    expect(view?.cursorIndex).toBe(0);
  });
});
