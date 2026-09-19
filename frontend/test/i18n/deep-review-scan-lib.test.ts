import { describe, it, expect } from 'vitest';
import { isJsxPosition, scanCandidates } from '../../scripts/i18n/scan-lib.mjs';

interface Candidate {
  line: number;
  cls: string;
  text: string;
  ctx: string;
  key: string;
}

/**
 * FE-09 回归：JSX 表达式容器（`{cond ? '中文' : '中文'}`、属性表达式里的
 * 模板字面量）内的中文字面量此前归类为未标注的 expr-str/expr-tpl，扫描面
 * 看不见；现在统一标注为 jsx-expr 候选（isJsxPosition === true）。
 */
describe('scan-lib JSX 表达式中文检测（FE-09）', () => {
  it('三元表达式里的中文字面量标注为 jsx-expr', () => {
    const src =
      "export function A({ cond }: { cond: boolean }) {\n" +
      "  return <div>{cond ? '中文甲' : '中文乙'}</div>\n" +
      "}\n";
    const cands = scanCandidates(src) as Candidate[];
    const exprs = cands.filter((c) => c.cls === 'jsx-expr');
    expect(exprs.map((c) => c.text)).toEqual(['中文甲', '中文乙']);
    expect(exprs.every((c) => isJsxPosition(c.cls))).toBe(true);
    expect(exprs.every((c) => c.ctx === 'children')).toBe(true);
  });

  it('属性表达式里的模板字面量同样标注为 jsx-expr', () => {
    const src = 'export const A = () => <div title={`中文标题${1}`} />\n';
    const cands = scanCandidates(src) as Candidate[];
    const exprs = cands.filter((c) => c.cls === 'jsx-expr');
    expect(exprs.map((c) => c.text)).toEqual(['中文标题']);
    expect(exprs[0].ctx).toBe('attr');
  });

  it('对象字面量的中文文案键仍走 obj:<key> 归类', () => {
    const src =
      "export function A() {\n" +
      "  return <div>{[{ label: '中文标签' }].map((x) => x.label)}</div>\n" +
      "}\n";
    const cands = scanCandidates(src) as Candidate[];
    expect(cands.some((c) => c.cls === 'obj' && c.key === 'label')).toBe(true);
  });
});
