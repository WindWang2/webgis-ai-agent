/**
 * label-grid 测试（V11 W4.2/4.5，ADR-0164）：交互侧网格碰撞的重叠率下降
 * （任务书 W4 验收：较 V10 无避让基线 ≥40%）、确定性、字段兜底。
 */
import { describe, it, expect } from 'vitest';
import {
  solveGridCollision,
  placeAllWithoutCollision,
  pairwiseOverlapRate,
  pickLabelField,
  labelPriorityScore,
  estimateLabelBoxEm,
  type GridLabelInput,
} from './label-grid';

const VP: [number, number, number, number] = [0, 0, 800, 600];

/** 确定性密集点阵（无随机：双曲格网 + 正弦扰动，簇间紧邻）。 */
function denseInputs(n = 200): GridLabelInput[] {
  const inputs: GridLabelInput[] = [];
  for (let i = 0; i < n; i += 1) {
    const col = i % 20;
    const row = Math.floor(i / 20);
    inputs.push({
      id: `p${i}`,
      text: `站点${i}号`,
      x: 20 + col * 38 + (row % 3) * 6,
      y: 20 + row * 26 + (col % 4) * 4,
      kind: 'point',
      priority: i % 5,
      fontSize: 12,
    });
  }
  return inputs;
}

describe('交互侧网格碰撞（W4.2）', () => {
  it('重叠率较无避让基线下降 ≥40%（W4 验收硬指标）', () => {
    const inputs = denseInputs(200);
    const baseline = placeAllWithoutCollision(inputs);
    const solved = solveGridCollision(inputs, { viewport: VP });
    const rateBase = pairwiseOverlapRate(inputs, baseline);
    const rateSolved = pairwiseOverlapRate(inputs, solved);
    expect(rateBase).toBeGreaterThan(0);
    const reduction = (rateBase - rateSolved) / rateBase;
    expect(reduction).toBeGreaterThanOrEqual(0.4);
    // 伴随硬约束（评审 finding）：防「全抑制刷重叠率」—— 放置率必须
    // 保持可观水平（密集阵实测 ~七成；下限 0.5 留余量）
    const placed = solved.filter((p) => p.status === 'placed').length;
    expect(placed / inputs.length).toBeGreaterThanOrEqual(0.5);
  });

  it('优先级评分：重要性/类别/面积（W4.4 避让优先级体系）', () => {
    const small = labelPriorityScore({ importance: 0.9, category: 'primary', areaPx: 100 });
    const big = labelPriorityScore({ importance: 0.9, category: 'primary', areaPx: 1e6 });
    const minor = labelPriorityScore({ importance: 0.2, category: 'decoration', areaPx: 100 });
    expect(small).toBeLessThan(big); // 面积大 → 成本高 → 分大（后放）
    expect(small).toBeLessThan(minor); // 小值优先：重要要素先放
    expect(labelPriorityScore({ importance: 0.5 })).toBe(
      labelPriorityScore({ importance: 0.5 })); // 确定性
  });

  it('确定性：同输入两次求解逐位相等', () => {
    const inputs = denseInputs(60);
    expect(solveGridCollision(inputs, { viewport: VP }))
      .toEqual(solveGridCollision(inputs, { viewport: VP }));
  });

  it('稳定性：输入序无关（输出按输入序回填）', () => {
    const inputs = denseInputs(50);
    const reversed = [...inputs].reverse();
    const a = solveGridCollision(inputs, { viewport: VP });
    const b = solveGridCollision(reversed, { viewport: VP });
    const byId = new Map(b.map((p) => [p.id, p]));
    for (const p of a) {
      expect(byId.get(p.id)).toEqual(p);
    }
  });

  it('空文本抑制；盒估算 CJK 1.0em / 其他 0.6em', () => {
    const out = solveGridCollision(
      [{ id: 'e', text: '  ', x: 10, y: 10, kind: 'point', priority: 0, fontSize: 12 }],
      { viewport: VP },
    );
    expect(out[0].status).toBe('suppressed');
    expect(estimateLabelBoxEm('两个').w).toBeCloseTo(2.0);
    expect(estimateLabelBoxEm('ab').w).toBeCloseTo(1.2);
  });
});

describe('前端字段兜底（W4.5）', () => {
  it('命中缺省词表并标 degraded', () => {
    const choice = pickLabelField([
      { code: 'A', name: '朝阳区' },
      { code: 'B', name: '海淀区' },
    ]);
    expect(choice).toEqual({ field: 'name', degraded: true });
  });

  it('满值字段优先于部分空值（扫描序 + 非空计数）', () => {
    const choice = pickLabelField([
      { name: '', title: '甲' },
      { name: '乙', title: '乙' },
    ]);
    expect(choice?.field).toBe('title');
  });

  it('无候选字段 → null（诚实不猜）', () => {
    expect(pickLabelField([{ code: 'A' }])).toBeNull();
    expect(pickLabelField([])).toBeNull();
  });
});
