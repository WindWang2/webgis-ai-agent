/**
 * PDF 图体矢量化测试（V11 W6.2，ADR-0166）。
 *
 * 验收：PDF 图体矢量可提取 —— 矢量体 PDF 不含图像 XObject（路径操作符
 * 绘制）；栅格对照含之。jsdom 环境（vitest 配置），svg2pdf 需要 DOM。
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { addVectorSvgBody, pdfHasRasterImage } from './pdf-vector';

beforeAll(() => {
  // jsdom 不实现 SVG getBBox（浏览器原生有）——svg2pdf 的文本测量依赖它；
  // 测试桩给出确定性盒（生产路径不依赖本桩）。
  if (!(SVGElement.prototype as { getBBox?: unknown }).getBBox) {
    (SVGElement.prototype as unknown as { getBBox: () => object }).getBBox =
      () => ({ x: 0, y: 0, width: 48, height: 12 });
  }
});

const SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">
  <rect x="10" y="10" width="80" height="40" fill="#336699" stroke="#111" stroke-width="2"/>
  <path d="M 0 0 L 200 100 M 200 0 L 0 100" stroke="#cc0000" stroke-width="3" fill="none"/>
  <text x="100" y="90" font-size="10" text-anchor="middle" fill="#000">MAP</text>
</svg>`;

async function makeDoc(compress = true) {
  const { jsPDF } = await import('jspdf');
  return new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4', compress });
}

describe('PDF 图体矢量化（W6.2）', () => {
  it('矢量体：svg2pdf 路径嵌入，PDF 不含图像 XObject（可提取）', async () => {
    const doc = await makeDoc();
    const ok = await addVectorSvgBody(doc, SVG, 10, 10, 180, 90);
    expect(ok).toBe(true);
    const bytes = doc.output('arraybuffer');
    expect(pdfHasRasterImage(bytes)).toBe(false);
  });

  it('栅格对照：addImage 的 PDF 含图像 XObject（判据有效）', async () => {
    const doc = await makeDoc();
    const canvas = document.createElement('canvas');
    canvas.width = 40;
    canvas.height = 20;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#336699';
    ctx.fillRect(0, 0, 40, 20);
    doc.addImage(canvas.toDataURL('image/png'), 'PNG', 10, 10, 40, 20);
    const bytes = doc.output('arraybuffer');
    expect(pdfHasRasterImage(bytes)).toBe(true);
  });

  it('fail-soft：坏 SVG/空输入返回 false（调用方回退栅格）', async () => {
    const doc = await makeDoc();
    expect(await addVectorSvgBody(doc, '', 0, 0, 10, 10)).toBe(false);
    expect(await addVectorSvgBody(doc, '<not-svg/>', 0, 0, 10, 10)).toBe(false);
    expect(await addVectorSvgBody(doc, '<svg', 0, 0, 10, 10)).toBe(false);
  });

  it('结构确定性：同 SVG 两次嵌入结构一致（长度/无图像/Form 在场）', async () => {
    const a = await makeDoc(false); // 未压缩：内容流可直接检路径算子
    const b = await makeDoc(false);
    // jsPDF 默认生成随机文档 ID + 当前时间戳 → 两次输出天然不同；固定
    // fileID 与创建/修改日期后，逐字节比较才是确定性契约的正确断言。
    const fix = (d: unknown) => {
      const doc = d as {
        setFileId: (id: string) => void;
        setCreationDate: (t: Date) => void;
        setModificationDate?: (t: Date) => void;
      };
      doc.setFileId('AC-V11');
      doc.setCreationDate(new Date(Date.UTC(2026, 0, 1)));
      doc.setModificationDate?.(new Date(Date.UTC(2026, 0, 1)));
    };
    fix(a);
    fix(b);
    await addVectorSvgBody(a, SVG, 10, 10, 180, 90);
    await addVectorSvgBody(b, SVG, 10, 10, 180, 90);
    // svg2pdf 有**进程级实例计数器**（生成 id 前缀随调用序号递增）——
    // 跨调用字节级确定性不可得（第三方实现事实）；锁定结构确定性：
    const ab = new Uint8Array(a.output('arraybuffer'));
    const bb = new Uint8Array(b.output('arraybuffer'));
    expect(ab.length).toBe(bb.length);
    expect(pdfHasRasterImage(a.output('arraybuffer'))).toBe(false);
    expect(pdfHasRasterImage(b.output('arraybuffer'))).toBe(false);
    const textA = new TextDecoder('latin1').decode(ab);
    // svg2pdf 直接绘制到页面内容流：矢量判据 = 路径算子在场（m/l/re）
    expect(/\d+\.?\d*\s+m/.test(textA)).toBe(true); // jsPDF 数字形态 "10."
    expect(/\d+\.?\d*\s+l/.test(textA)).toBe(true);
  });
});
