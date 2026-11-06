/**
 * ADR-0283 D3 · #1910 非重叠前端验收 —— PDF 文本层披露诚实语义。
 *
 * #1910 的后端缺陷（WeasyPrint deny-all fetcher 拦截 data: 字体却仍写
 * pdf_cjk_font_embedded 回执）修复归属他人；本文件锁定**前端面不变式**：
 * 字体未成功嵌入时绝不发布 pdf_cjk_font_embedded（栅格化兜底披露取而代之）。
 * 后端回执一致性核对（tests/cartography/test_pdf_receipt_parity_v1.py）以
 * 本函数语义为对拍基准。
 */
import { describe, it, expect } from 'vitest';
import { decidePdfTextLayerDisclosure } from './exporter';

describe('decidePdfTextLayerDisclosure（#1910 前端验收不变式）', () => {
  it('字体已嵌入 + 含 CJK → pdf_cjk_font_embedded（可选取/可检索）', () => {
    const out = decidePdfTextLayerDisclosure(true, true, true);
    expect(out).toHaveLength(1);
    expect(out[0].code).toBe('pdf_cjk_font_embedded');
  });

  it('字体未嵌入 + 文本层 vector 不可用 + 含 CJK → pdf_text_rasterized_cjk（不谎报嵌入）', () => {
    const out = decidePdfTextLayerDisclosure(false, false, true);
    expect(out).toHaveLength(1);
    expect(out[0].code).toBe('pdf_text_rasterized_cjk');
  });

  it('字体未嵌入但 vector 文本层可用（纯 WinAnsi 文本）→ 无披露条目', () => {
    expect(decidePdfTextLayerDisclosure(false, true, false)).toEqual([]);
  });

  it('字体未嵌入 + 纯 WinAnsi（无 CJK）→ 即使 vector 关闭也不发栅格化披露', () => {
    // 语义：rasterized_cjk 披露只对"确有非 WinAnsi 字符"的场景成立；
    // 纯 ASCII 标题栅格化原因不是 CJK（避免误导性归因）。
    expect(decidePdfTextLayerDisclosure(false, false, false)).toEqual([]);
  });

  it('字体已嵌入 + 纯 WinAnsi → 不发嵌入披露（嵌入无从谈起收益，不噪音化）', () => {
    expect(decidePdfTextLayerDisclosure(true, true, false)).toEqual([]);
  });
});
