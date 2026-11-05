/**
 * ADR-0283 D2 · 关键文本 wrap-not-clip —— canvas chrome 绘制面。
 *
 * - drawChromeText：el.lines（>1 行）逐行绘制（行距 fontPx×1.3 向下取整），
 *   单行元素渲染逐调用不变；
 * - bottom 锚定多行块整体上移（块底贴锚线，不向下溢出画幅）；
 * - vector-svg-export：wrapped 条目按 lines 逐行输出 <text>（长标题不横向
 *   溢出图幅）。
 */
import { describe, it, expect } from 'vitest';
import { drawChromeText } from './export-chrome';
import type { DrawCtx } from './export-chrome';

function mockCtx() {
  const calls: Array<{ op: string; args: unknown[] }> = [];
  const ctx = new Proxy({}, {
    get(_t, prop: string) {
      if (prop === 'fillText' || prop === 'textAlign') {
        return (...args: unknown[]) => { calls.push({ op: prop, args }); };
      }
      return () => {};
    },
    set() { return true; },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, calls };
}

const DRAW_BASE = {
  ctx: null as unknown as CanvasRenderingContext2D,
  targetW: 1600,
  targetH: 1200,
  scalePx: (v: number) => v,
  darkMode: false,
  style: { fontFamily: 'sans-serif', titleColor: '#000', accentColor: '#e53e3e' },
} as unknown as DrawCtx & { ctx: CanvasRenderingContext2D };

describe('drawChromeText wrap-not-clip（ADR-0283 D2）', () => {
  it('多行元素逐行绘制，行距 = round(fontPx×1.3)', () => {
    const { ctx, calls } = mockCtx();
    drawChromeText(
      { ...DRAW_BASE, ctx },
      {
        kind: 'title', anchor: 'top-center',
        text: '长标题全文……',
        lines: ['第一行', '第二行'],
      },
      32, '#000', { marginX: 40, marginY: 52 },
    );
    const texts = calls.filter((c) => c.op === 'fillText');
    expect(texts.length).toBe(2);
    expect(texts[0].args[0]).toBe('第一行');
    expect(texts[1].args[0]).toBe('第二行');
    expect(texts[1].args[2] - texts[0].args[2]).toBe(Math.round(32 * 1.3));
  });

  it('单行元素渲染不变（一次 fillText，无行偏移）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeText(
      { ...DRAW_BASE, ctx },
      { kind: 'title', anchor: 'top-center', text: 'T' },
      32, '#000', { marginX: 40, marginY: 52 },
    );
    const texts = calls.filter((c) => c.op === 'fillText');
    expect(texts.length).toBe(1);
    expect(texts[0].args[0]).toBe('T');
  });

  it('bottom 锚定多行块整体上移（首行抬高 step×(n-1)，块底贴锚线）', () => {
    const { ctx, calls } = mockCtx();
    drawChromeText(
      { ...DRAW_BASE, ctx },
      {
        kind: 'subtitle', anchor: 'bottom-right',
        text: 'x', lines: ['a', 'b', 'c'],
      },
      20, '#333', { marginX: 40, marginY: 30 },
    );
    const texts = calls.filter((c) => c.op === 'fillText');
    expect(texts.length).toBe(3);
    const step = Math.round(20 * 1.3);
    const y0 = texts[0].args[2] as number;
    const y2 = texts[2].args[2] as number;
    expect(y2 - y0).toBe(step * 2);
    // 第三行（末行）基线 = targetH - marginY（块底贴锚线语义）
    expect(y2).toBe(DRAW_BASE.targetH - 30);
  });
});
