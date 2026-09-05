/**
 * V3（ADR-0101 D6）— 披露族 canvas 导出契约：
 * methodology_note / uncertainty_panel / decision_panel 从「仅 interactive」
 * 升级为导出可消费（drawChromeDisclosurePanel）。与 live 渲染器同一防御式
 * 解析语义：坏载荷 → 面板缺席（不伪造）；collapsed → 折叠标题条（E-2）。
 */
import { describe, expect, it } from 'vitest';
import { buildExportChrome, drawChromeDisclosurePanel } from './export-chrome';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

const CANVAS = { width: 1600, height: 1200 };
const VIEWPORT = { width: 800, height: 600 };

function comp(partial: Partial<MapSpecComponent> & { id: string; type: MapSpecComponent['type'] }): MapSpecComponent {
  return partial as MapSpecComponent;
}

function mockCtx() {
  const calls: Array<{ op: string; args: unknown[] }> = [];
  const ctx: any = {
    fillText: (...a: unknown[]) => calls.push({ op: 'fillText', args: a }),
    fillRect: (...a: unknown[]) => calls.push({ op: 'fillRect', args: a }),
    strokeRect: (...a: unknown[]) => calls.push({ op: 'strokeRect', args: a }),
    beginPath: (...a: unknown[]) => calls.push({ op: 'beginPath', args: a }),
    moveTo: (...a: unknown[]) => calls.push({ op: 'moveTo', args: a }),
    lineTo: (...a: unknown[]) => calls.push({ op: 'lineTo', args: a }),
    arc: (...a: unknown[]) => calls.push({ op: 'arc', args: a }),
    arcTo: (...a: unknown[]) => calls.push({ op: 'arcTo', args: a }),
    closePath: (...a: unknown[]) => calls.push({ op: 'closePath', args: a }),
    fill: (...a: unknown[]) => calls.push({ op: 'fill', args: a }),
    stroke: (...a: unknown[]) => calls.push({ op: 'stroke', args: a }),
    save: () => {},
    restore: () => {},
    set fillStyle(v: unknown) { calls.push({ op: 'fillStyle', args: [v] }); },
    get fillStyle() { return ''; },
    set font(v: unknown) { calls.push({ op: 'font', args: [v] }); },
    get font() { return ''; },
    set textAlign(v: unknown) { calls.push({ op: 'textAlign', args: [v] }); },
    get textAlign() { return ''; },
    measureText: (s: string) => ({ width: s.length * 6 }),
  };
  return { ctx, calls };
}

describe('buildExportChrome — 披露族面板归一化', () => {
  it('methodology_note：warnings → 警示披露卡（code 前缀保留）', async () => {
    const model = await buildExportChrome(
      {
        spec: { layout: { components: [comp({
          id: 'methodology', type: 'methodology_note',
          options: { warnings: [{ code: 'MISSING_DENOM', text: '缺分母不能谈公平性' }] },
        })] } },
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    const panel = model.panels.find((p) => p.kind === 'methodology');
    expect(panel).toBeDefined();
    expect(panel?.disclosure?.title).toBe('方法论披露');
    expect(panel?.disclosure?.accent).toBe(true);
    expect(panel?.disclosure?.rows[0]).toContain('MISSING_DENOM');
    expect(panel?.disclosure?.rows[0]).toContain('缺分母不能谈公平性');
  });

  it('uncertainty_panel：items + sampleNote → 行归一化', async () => {
    const model = await buildExportChrome(
      {
        spec: { layout: { components: [comp({
          id: 'unc', type: 'uncertainty_panel',
          options: { uncertainty: {
            items: [{ label: 'IDW 插值', kind: 'variance', detail: 'σ²=0.4' }],
            sampleNote: '样本 12 个，低于建议下限',
          } },
        })] } },
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    const panel = model.panels.find((p) => p.kind === 'uncertainty');
    expect(panel?.disclosure?.rows).toHaveLength(2);
    expect(panel?.disclosure?.rows[0]).toContain('方差');
    expect(panel?.disclosure?.rows[1]).toContain('样本 12 个');
  });

  it('decision_panel：method 入标题、weightSource 首行、vetoed 删除线、行不封顶', async () => {
    const model = await buildExportChrome(
      {
        spec: { layout: { components: [comp({
          id: 'dec', type: 'decision_panel',
          options: { decision: {
            method: 'MCDA',
            weightSource: '用户设定',
            rows: [
              { rank: 1, name: '地块 A', score: 0.87, basis: 'observed' },
              { rank: 2, name: '地块 B', score: 0.42, basis: 'vetoed' },
              ...Array.from({ length: 15 }, (_, i) => ({ rank: i + 3, name: `候选 ${i + 3}`, score: 0.1 })),
            ],
            vetoes: ['不得占用永久基本农田'],
          } },
        })] } },
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    const panel = model.panels.find((p) => p.kind === 'decision');
    expect(panel?.disclosure?.title).toBe('决策（MCDA）');
    const rows = panel?.disclosure?.rows ?? [];
    // 与 live 同序：weightSource → 全部排名行（不封顶）→ 否决段
    expect(rows[0]).toContain('权重来源：用户设定');
    expect(rows.filter((r) => r.includes('候选')).length).toBe(15);
    expect(rows.some((r) => r.includes('硬约束否决：'))).toBe(true);
    expect(rows.some((r) => r.includes('· 不得占用永久基本农田'))).toBe(true);
    // basis 仅区分 vetoed（删除线），不做内联文本（与 live data-basis 同义）
    expect(panel?.disclosure?.strikeRows).toEqual([2]);
    expect(rows[2]).not.toContain('vetoed');
  });

  it('坏载荷 → 面板缺席（与 live 空态语义一致）', async () => {
    const model = await buildExportChrome(
      {
        spec: { layout: { components: [
          comp({ id: 'm', type: 'methodology_note', options: { warnings: 'not-an-array' } }),
          comp({ id: 'u', type: 'uncertainty_panel', options: { uncertainty: { items: [] } } }),
          comp({ id: 'd', type: 'decision_panel', options: { decision: { rows: [] } } }),
        ] } },
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    expect(model.panels.filter((p) => ['methodology', 'uncertainty', 'decision'].includes(p.kind))).toHaveLength(0);
  });

  it('collapsed → 折叠标题条（E-2 约定；collapsed 是 placement 上的 mode 无关字段）', async () => {
    const model = await buildExportChrome(
      {
        spec: { layout: { components: [comp({
          id: 'm', type: 'methodology_note',
          placement: { mode: 'anchor', collapsed: true } as any,
          options: { warnings: [{ text: '披露行' }] },
        })] } },
        viewport: VIEWPORT,
        legendSpecsByLayer: {},
      },
      CANVAS,
    );
    const panel = model.panels.find((p) => p.kind === 'methodology');
    expect(panel?.text).toBe('方法论披露');
  });
});

describe('drawChromeDisclosurePanel — 画布绘制', () => {
  it('绘制标题 + 每行文本（methodology 带警示标记条）', () => {
    const { ctx, calls } = mockCtx();
    const d = { ctx, darkMode: false, scalePx: (v: number) => v, targetW: 1600, targetH: 1200 };
    drawChromeDisclosurePanel(d as any, {
      kind: 'methodology', anchor: 'bottom-left',
      disclosure: { title: '方法论披露', rows: ['行一', '行二'], accent: true },
    } as any, { marginX: 40 });
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toContain('方法论披露');
    expect(texts).toContain('行一');
    expect(texts).toContain('行二');
    // accent 标记条（fillRect）
    expect(calls.some((c) => c.op === 'fillRect')).toBe(true);
  });

  it('collapsed 只画标题条', () => {
    const { ctx, calls } = mockCtx();
    const d = { ctx, darkMode: false, scalePx: (v: number) => v, targetW: 1600, targetH: 1200 };
    drawChromeDisclosurePanel(d as any, {
      kind: 'uncertainty', anchor: 'bottom-right', text: '不确定性',
      disclosure: { title: '不确定性', rows: ['不应出现'], accent: false },
    } as any, { marginX: 40 });
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    expect(texts).toContain('不确定性');
    expect(texts).not.toContain('不应出现');
  });

  it('formatImperialLabel：英制换算与 live 同式', async () => {
    const { formatImperialLabel } = await import('./export-chrome');
    expect(formatImperialLabel(1000)).toBe('3281 ft');
    expect(formatImperialLabel(1610)).toBe('1.0 mi');
    expect(formatImperialLabel(16100)).toBe('10 mi');
  });

  it('超宽行确定性截断（… 尾）', () => {
    const { ctx, calls } = mockCtx();
    const d = { ctx, darkMode: false, scalePx: (v: number) => v, targetW: 1600, targetH: 1200 };
    const longRow = '超'.repeat(200);
    drawChromeDisclosurePanel(d as any, {
      kind: 'decision', anchor: 'top-left',
      disclosure: { title: '决策', rows: [longRow], accent: false },
    } as any, { marginX: 40 });
    const texts = calls.filter((c) => c.op === 'fillText').map((c) => String(c.args[0]));
    const clipped = texts.find((t) => t.endsWith('…'));
    expect(clipped).toBeDefined();
    expect(clipped!.length).toBeLessThan(longRow.length);
  });
});
