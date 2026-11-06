/**
 * ADR-0283 D2 · 关键文本 wrap-not-clip —— 双侧孪生对拍（TS 参照面）。
 *
 * - 折行语义：超长 CJK/拉丁标题折行（复用 parity 原语 wrapLabelMultilingual），
 *   内容不减少；折行预算用尽才末行可见截断（truncated）。
 * - 兼容形态：单行未截断 = 旧 IR 逐字段一致（不携带 wrapped/lines 增量键）。
 * - golden：UPDATE_LAYOUT_GOLDEN=1 时以 TS 参照实现生成/再生
 *   tests/cartography/golden_corpus/layout_description/long_title_wrap.json；
 *   常规模式消费该 fixture 深等断言（Python 镜像测试消费同一份 expected）。
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { buildPublicationLayout } from './layout-description';

const FIXTURE_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../tests/cartography/golden_corpus/layout_description',
);
const FIXTURE = path.join(FIXTURE_DIR, 'long_title_wrap.json');

const longCjkTitle =
  '二〇二六年秋季华北平原城市群基础教育资源空间分异研究：基于多源地理开放数据的区县级统计与择校可达性综合评估报告'
  + '（覆盖京津冀鲁豫五省市一百零二个区县、四千三百余所小学与初级中学的分布、师资生均比、择校通勤可达性与学区房价梯度的联合分析）';
const longLatinSubtitle =
  'A comprehensive spatial analysis of school distribution, district-level statistics and accessibility across the metropolitan region in autumn 2026, '
  + 'covering one hundred and two county-level administrative units, more than four thousand three hundred primary and secondary schools, teacher-student ratios, commuting accessibility and school-district housing price gradients.';

function buildInput() {
  return {
    paperSize: 'A4' as const,
    orientation: 'landscape' as const,
    dpi: 300,
    frame: { width: 2480, height: 1754 },
    chromeModel: null,
    requestTitle: longCjkTitle,
    requestSubtitle: longLatinSubtitle,
    author: 'webgis-ai-agent',
    metersPerPixel: 20.3,
    extent: {
      mask: [116.1, 39.7, 116.7, 40.1] as [number, number, number, number],
      export: [116.05, 39.65, 116.75, 40.15] as [number, number, number, number],
      dataOverflow: false,
    },
    colorMode: 'cmyk' as const,
    safeAreaMm: { top: 12, right: 10, bottom: 15, left: 10 },
  };
}

function jsonProjection(ir: ReturnType<typeof buildPublicationLayout>) {
  const { chromeModel: _canvasOnly, ...jsonIr } = ir as typeof ir & {
    chromeModel?: unknown;
  };
  void _canvasOnly;
  return jsonIr;
}

describe('layout-description wrap-not-clip（ADR-0283 D2）', () => {
  it('超长 CJK 标题折行不截断：wrapped+lines、无 truncated、内容完整', () => {
    const ir = buildPublicationLayout(buildInput());
    const title = ir.texts.find((t) => t.kind === 'title')!;
    expect(title.wrapped).toBe(true);
    expect(title.truncated).toBeUndefined();
    expect(title.lines!.length).toBe(2);
    // 内容完整（忽略空白逐字符等价 —— 不以减少文字掩盖溢出）
    expect(title.lines!.join('').replace(/\s/g, '')).toBe(
      longCjkTitle.replace(/\s/g, ''),
    );
  });

  it('超长拉丁副标题按词折行', () => {
    const ir = buildPublicationLayout(buildInput());
    const subtitle = ir.texts.find((t) => t.kind === 'subtitle')!;
    expect(subtitle.wrapped).toBe(true);
    expect(subtitle.lines!.length).toBeGreaterThan(1);
    expect(subtitle.lines!.join(' ').replace(/\s/g, '')).toBe(
      longLatinSubtitle.replace(/\s/g, ''),
    );
  });

  it('短文本保持旧形态（不携带增量键，golden 逐字节兼容）', () => {
    const input = {
      ...buildInput(),
      requestTitle: '短标题',
      requestSubtitle: '短副标题',
    };
    const ir = buildPublicationLayout(input);
    expect(ir.texts.find((t) => t.kind === 'title')).toEqual({
      kind: 'title',
      text: '短标题',
    });
    expect(ir.texts.find((t) => t.kind === 'subtitle')).toEqual({
      kind: 'subtitle',
      text: '短副标题',
    });
    expect(ir.page.safeAreaMm).toEqual({ top: 12, right: 10, bottom: 15, left: 10 });
  });

  it('safeAreaMm 缺席时 page 不携带该键（旧 IR 形态）', () => {
    const { safeAreaMm: _omit, ...input } = buildInput();
    void _omit;
    const ir = buildPublicationLayout(input);
    expect('safeAreaMm' in ir.page).toBe(false);
  });

  it('折行预算耗尽才末行可见截断（truncated 与 wrapped 并存）', () => {
    const huge = '测'.repeat(400);
    const ir = buildPublicationLayout({ ...buildInput(), requestTitle: huge });
    const title = ir.texts.find((t) => t.kind === 'title')!;
    expect(title.wrapped).toBe(true);
    expect(title.truncated).toBe(true);
    expect(title.lines!.length).toBe(2);
    // 末行被截断：内容确有丢失（诚实披露路径）
    expect(title.lines!.join('').replace(/\s/g, '').length).toBeLessThan(400);
  });

  it('golden：expected 与参照实现一致（或以 UPDATE_LAYOUT_GOLDEN=1 再生）', () => {
    const expected = jsonProjection(buildPublicationLayout(buildInput()));
    if (process.env.UPDATE_LAYOUT_GOLDEN === '1') {
      const doc = {
        name: 'layout_description_long_title_wrap',
        _provenance:
          'ADR-0283 D2 —— expected 由 TS 参照实现（frontend/lib/export/layout-description.ts）生成并冻结（UPDATE_LAYOUT_GOLDEN=1 再生）；pytest 镜像（app/lib/cartography/layout_description.py）须逐字段复现。覆盖：超长 CJK/拉丁标题折行、safeAreaMm 增量、cmyk 出血档。',
        input: buildInput(),
        expected,
      };
      writeFileSync(FIXTURE, `${JSON.stringify(doc, null, 2)}\n`, 'utf-8');
      return;
    }
    expect(existsSync(FIXTURE)).toBe(true);
    const fixture = JSON.parse(readFileSync(FIXTURE, 'utf-8'));
    expect(expected).toEqual(fixture.expected);
  });
});
