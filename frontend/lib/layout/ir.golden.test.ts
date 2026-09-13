/**
 * C2 LayoutDescription IR golden 对拍 —— TS 侧（V11 W0.1，ADR-0160）。
 *
 * 与 tests/cartography/test_layout_ir_golden.py 消费**同一份** fixture：
 * expected 由 Python 权威实现（build_layout_ir）生成冻结，本侧镜像须逐
 * 字段复现 —— 参照实现漂移即红灯（与 ADR-0157 layout-description 对拍
 * 同纪律）。
 */
import { describe, it, expect } from 'vitest';
import fixture from '../../../tests/cartography/golden_corpus/layout_ir/basic.json';
import {
  buildLayoutIr,
  validateLayoutIr,
  LAYOUT_IR_VERSION,
  type LayoutIrComponentInput,
} from './ir';

const input = fixture.input as {
  canvas: { widthPx: number; heightPx: number };
  components: LayoutIrComponentInput[];
  degradations: Array<{ code: string; detail: string }>;
};

describe('C2 layout IR golden（ADR-0160 双端对拍）', () => {
  it('TS 镜像复现 expected（逐字段深等）', () => {
    const ir = buildLayoutIr({
      canvas: input.canvas,
      components: input.components,
      degradations: input.degradations,
    });
    expect(ir).toEqual(fixture.expected);
  });

  it('确定性：同输入两次装配逐字段相等', () => {
    const a = buildLayoutIr({ canvas: input.canvas, components: input.components });
    const b = buildLayoutIr({ canvas: input.canvas, components: input.components });
    expect(a).toEqual(b);
  });

  it('layers z 升序；z=30 并列层按 id 字典序稳定排（跨语言 tie-break）', () => {
    const zs = fixture.expected.layers.map((l) => l.z);
    expect(zs).toEqual([...zs].sort((a, b) => a - b));
    const tied = fixture.expected.layers.filter((l) => l.z === 30).map((l) => l.id);
    expect(tied).toEqual([...tied].sort());
  });

  it('fixture IR 通过结构校验；版本锁 2', () => {
    expect(LAYOUT_IR_VERSION).toBe(2);
    expect(validateLayoutIr(fixture.expected as never)).toEqual([]);
  });

  it('fail-closed：非法 kind / 出界 frame / 重复 id 被拒绝', () => {
    const ir = buildLayoutIr({
      canvas: { widthPx: 100, heightPx: 100 },
      components: [
        { id: 'c1', kind: 'title', frame: { x: 0, y: 0, width: 40, height: 10, z: 1 } },
      ],
    });
    expect(validateLayoutIr(ir)).toEqual([]);

    const badKind = structuredClone(ir);
    badKind.components[0].kind = 'unicorn' as never;
    expect(validateLayoutIr(badKind).some((m) => m.includes('kind 非法'))).toBe(true);

    const outOfCanvas = buildLayoutIr({
      canvas: { widthPx: 100, heightPx: 100 },
      components: [
        { id: 'c1', kind: 'title', frame: { x: 90, y: 0, width: 40, height: 10, z: 1 } },
      ],
    });
    expect(validateLayoutIr(outOfCanvas).some((m) => m.includes('超出画布'))).toBe(true);

    const dupId = buildLayoutIr({
      canvas: { widthPx: 100, heightPx: 100 },
      components: [
        { id: 'c1', kind: 'title', frame: { x: 0, y: 0, width: 10, height: 10, z: 1 } },
        { id: 'c1', kind: 'legend', frame: { x: 0, y: 0, width: 10, height: 10, z: 2 } },
      ],
    });
    expect(validateLayoutIr(dupId).some((m) => m.includes('id 重复'))).toBe(true);
  });
});
