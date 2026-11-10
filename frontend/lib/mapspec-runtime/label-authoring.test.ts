/**
 * G16：authoring → MapLibre 表达式投影单元（身份/分组 match/orphan）。
 */
import { describe, expect, it } from 'vitest';
import {
  buildAutoFilter, buildHiddenExcludeFilter, buildPinnedFilter,
  buildTextFieldExpr, buildTextOffsetExpr, projectAuthoring,
} from './label-authoring';

const feats = [
  { id: 's1', properties: { name: '第一' } },
  { id: 's2', properties: { name: '第二' } },
  { id: 's3', properties: { name: '第三' } },
  { properties: { OBJECTID: 7, name: '第四' } },
];

describe('projectAuthoring（G16 live 投影）', () => {
  it('orphan 不投影、active 分物料', () => {
    const proj = projectAuthoring([
      { featureId: 's1', hidden: true, idKey: null },
      { featureId: 'ghost', hidden: true, idKey: null },
      { featureId: 's2', pinned: true, offset: { dx: 1, dy: 2 }, idKey: null },
    ], feats);
    expect(proj.active.map((o) => o.featureId)).toEqual(['s1', 's2']);
    expect(proj.orphan.map((o) => o.featureId)).toEqual(['ghost']);
    expect(proj.hiddenIds).toEqual(['s1']);
    expect(proj.pinned.map((o) => o.featureId)).toEqual(['s2']);
  });

  it('数字与字符串身份折叠（1 ≡ "1"）', () => {
    const proj = projectAuthoring([{ featureId: '7', pinned: true, idKey: 'OBJECTID' }], feats);
    expect(proj.active).toHaveLength(1);
    expect(proj.orphan).toHaveLength(0);
  });

  it('text 覆写 → match 表达式（含 id 与属性键分组）', () => {
    const proj = projectAuthoring([
      { featureId: 's1', text: '自定义一', idKey: null },
      { featureId: '7', text: '自定义七', idKey: 'OBJECTID' },
    ], feats);
    const expr = buildTextFieldExpr(proj, 'name') as unknown[];
    expect(expr).toBeTruthy();
    const flat = JSON.stringify(expr);
    expect(flat).toContain('"自定义一"');
    expect(flat).toContain('"自定义七"');
    expect(flat).toContain('OBJECTID');
  });

  it('hidden → match false；pinned → match true；auto filter 排除两者', () => {
    const proj = projectAuthoring([
      { featureId: 's1', hidden: true, idKey: null },
      { featureId: 's2', pinned: true, idKey: null },
    ], feats);
    expect(JSON.stringify(buildHiddenExcludeFilter(proj))).toContain('false');
    expect(JSON.stringify(buildPinnedFilter(proj))).toContain('true');
    const auto = JSON.stringify(buildAutoFilter(proj));
    expect(auto).toBeTruthy();
  });

  it('offset → text-offset match（[dx,dy] em 字面量）', () => {
    const proj = projectAuthoring([
      { featureId: 's2', pinned: true, offset: { dx: 1.5, dy: -0.5 }, idKey: null },
    ], feats);
    const expr = JSON.stringify(buildTextOffsetExpr(proj, true));
    expect(expr).toContain('[1.5,-0.5]');
  });

  it('数据不可复核（外部源）→ 全量 unverified 投影', () => {
    const proj = projectAuthoring([{ featureId: 'x', hidden: true }], null);
    expect(proj.active).toHaveLength(1);
    expect(proj.orphan).toHaveLength(0);
  });
});
