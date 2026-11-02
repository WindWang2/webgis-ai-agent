/**
 * temporal-map 数据面联动测试（GOAL 03 M4 —— Oracle 4/7）。
 *
 * - 同窗 where：ref 匹配 temporal 绑定 → where AST（terms 语法）与地图
 *   filter 同值；
 * - 身份键稳定：同帧重渲染同 key（effect 不重取 → 零网络风暴）；换帧/
 *   换 revision 键变化；
 * - 非绑定 ref → 不附加（非时序层零影响）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { commitMapSpecDocument } from '@/lib/mapspec/session-cursor';
import { resetTemporalLiveState, setActiveController, TemporalPlaybackController, requestLiveFrame } from './live-frame';
import { mergeWhereAst, temporalWindowForRef, whereParam } from './linkage';

const temporal = {
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
  playback: { playing: false, rate: 1 },
  source_ref: 'ref:schools',
  content_revision: 7,
};

beforeEach(() => {
  resetTemporalLiveState();
  setActiveController(new TemporalPlaybackController());
  commitMapSpecDocument({ version: '1.8', layers: [], temporal }, 1);
});

afterEach(() => {
  resetTemporalLiveState();
});

describe('temporalWindowForRef', () => {
  it('绑定 ref → where AST（terms 语法，值与地图 filter 同源）', () => {
    const link = temporalWindowForRef('ref:schools');
    expect(link.key).toBeTruthy();
    const ast = link.where as { op: string; terms: { op: string; value: unknown }[] };
    expect(ast.op).toBe('and');
    expect(ast.terms[0]).toMatchObject({ op: 'gte', field: 'year', value: 2022 });
    expect(ast.terms[1]).toMatchObject({ op: 'lt', field: 'year', value: 2023 });
  });

  it('层绑定 source_ref 命中（layer.source_ref 匹配）', () => {
    expect(temporalWindowForRef('ref:other').key).toBe('');
  });

  it('非绑定 ref / 无 temporal → 零影响', () => {
    expect(temporalWindowForRef('ref:unrelated').where).toBeUndefined();
    expect(temporalWindowForRef('ref:unrelated').key).toBe('');
    expect(temporalWindowForRef(undefined).key).toBe('');
  });

  it('同帧键稳定（重复渲染零重取）；换帧键变化（M7 防风暴）', () => {
    const a = temporalWindowForRef('ref:schools').key;
    const b = temporalWindowForRef('ref:schools').key;
    expect(a).toBe(b); // 同帧同键 → aggregate/table effect deps 不变
    requestLiveFrame({ ...temporal.frames[2] });
    const c = temporalWindowForRef('ref:schools').key;
    expect(c).not.toBe(a);
  });

  it('live 帧覆盖 cursor 窗口（播放中统计跟随画布帧）', () => {
    requestLiveFrame({ ...temporal.frames[0] });
    const ast = temporalWindowForRef('ref:schools').where as { terms: { value: unknown }[] };
    expect(ast.terms[0].value).toBe(2021);
  });
});

describe('mergeWhereAst / whereParam', () => {
  it('搜索 × 时间窗 and 合成；单侧缺席直通', () => {
    const search = { op: 'eq', field: 'district', value: '区1' } as never;
    const time = temporalWindowForRef('ref:schools').where;
    const merged = mergeWhereAst(search, time) as { op: string; terms: unknown[] };
    expect(merged.op).toBe('and');
    expect(merged.terms).toHaveLength(2);
    expect(mergeWhereAst(undefined, time)).toBe(time);
    expect(mergeWhereAst(search, undefined)).toBe(search);
    expect(whereParam(merged)).toBe(JSON.stringify(merged));
    expect(whereParam(undefined)).toBeUndefined();
  });
});
