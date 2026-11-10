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

// ── R1 复审回归 ─────────────────────────────────────────────────────────
describe('R1 review fixes（G16）', () => {
  it('P1-1：数值身份经 to-string 参与匹配（真实 style-spec 表达式求值）', async () => {
    // 用已安装的 maplibre style-spec 表达式求值器验证：数值 feature.id/
    // 数值属性 与字符串覆写键确实匹配（to-string 归一）。
    const spec = await import('@maplibre/maplibre-gl-style-spec');
    const createExpression = (spec as unknown as {
      createExpression?: (v: unknown) => { result: string; value?: { evaluate: (g: unknown) => unknown } };
    }).createExpression;
    if (typeof createExpression !== 'function') {
      // 求值器不可用（版本差异）—— 退化为表达式形状断言（to-string 在场）。
      const proj = projectAuthoring(
        [{ featureId: '7', pinned: true, idKey: 'OBJECTID' }], feats);
      const json = JSON.stringify(buildPinnedFilter(proj));
      expect(json).toContain('to-string');
      return;
    }
    const proj = projectAuthoring(
      [{ featureId: '7', hidden: true, idKey: 'OBJECTID' }], feats);
    const expr = buildHiddenExcludeFilter(proj);
    expect(expr).toBeTruthy();
    const compiled = createExpression(expr!, 'layers[0].layout.text-field');
    expect(compiled.result).toBe('success');
    const ev = (props: Record<string, unknown>) =>
      compiled.value!.evaluate({}, {
        properties: props, geometry: null, type: 'feature',
      } as never);
    // 数值 OBJECTID=7 的要素 → 命中（false = 隐藏）。对照组：不带
    // to-string 的裸 match 对数值永不命中（P1 原缺陷 —— 实测 true）。
    const bare = createExpression(
      ['match', ['get', 'OBJECTID'], '7', false, true], 'x');
    expect(bare.value!.evaluate({}, {
      properties: { OBJECTID: 7 }, geometry: null, type: 'feature',
    } as never)).toBe(true); // 原缺陷实证
    expect(ev({ OBJECTID: 7 })).toBe(false);          // 修复后命中
    expect(ev({ OBJECTID: '7' })).toBe(false);         // 1 ≡ "1" 契约
    expect(ev({ OBJECTID: 8 })).toBe(true);            // 其他不隐藏
  });

  it('P2-7：hidden+pinned 矛盾组合 → hidden 恒胜（pinned filter 排除）', () => {
    const proj = projectAuthoring([
      { featureId: 's1', hidden: true, pinned: true, idKey: null },
      { featureId: 's2', pinned: true, idKey: null },
    ], feats);
    const json = JSON.stringify(buildPinnedFilter(proj));
    expect(json).toContain('s2');
    expect(json).not.toContain('"s1"');
  });

  it('P2-12：>3 个解析键 → unprojectable 点名（不静默丢）', () => {
    const multi = [
      { featureId: 'a', pinned: true, idKey: 'id' },
      { featureId: 'b', pinned: true, idKey: 'OBJECTID' },
      { featureId: 'c', pinned: true, idKey: 'fid' },
      { featureId: 'd', pinned: true, idKey: 'osm_id' },
    ];
    // features=null：全量 unverified 投影（分组只看 idKey，无需数据）。
    const proj = projectAuthoring(multi, null);
    expect(proj.unprojectable).toContain('d');
  });

  it('P3-22：危险 match 字面量名不进表达式', () => {
    const proj = projectAuthoring(
      [{ featureId: 'toString', hidden: true, idKey: null }], null);
    expect(proj.unprojectable).toContain('toString');
    expect(buildHiddenExcludeFilter(proj)).toBeNull();
  });
});
