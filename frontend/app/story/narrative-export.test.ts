/**
 * 叙事 PDF 导出编排（ADR-0147 §4/#1213 决策的验证面）。
 *
 * jspdf 用假体（模式同 lib/map-kit/exporter.pdf.test.ts 先例）——真实
 * exportToPDF 多页链跑通：逐章 capture → blob→canvas → pages 参数 →
 * 输出 Blob（落盘产物）。Image/URL 打桩让 blobToCanvas 在 jsdom 可行。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { exportNarrativePdf, type NarrativePdfChapterInput } from './narrative-export';

const jsdocCalls = { text: [] as string[], addImage: 0, addPage: 0 };

vi.mock('jspdf', () => ({
  default: class {
    internal = { pageSize: { getWidth: () => 297, getHeight: () => 210 } };
    addImage = vi.fn(() => {
      jsdocCalls.addImage += 1;
    });
    addPage = vi.fn(() => {
      jsdocCalls.addPage += 1;
    });
    setFontSize = vi.fn();
    setTextColor = vi.fn();
    text = vi.fn((s: string) => jsdocCalls.text.push(String(s)));
    setProperties = vi.fn();
    setDrawColor = vi.fn();
    setLineWidth = vi.fn();
    rect = vi.fn();
    output = vi.fn(
      () => new Blob(['%PDF-1.4 narrative-test'], { type: 'application/pdf' }),
    );
  },
}));

beforeEach(() => {
  jsdocCalls.text = [];
  jsdocCalls.addImage = 0;
  jsdocCalls.addPage = 0;
});

function fakePngBlob(): Blob {
  return new Blob(['fake-png-bytes'], { type: 'image/png' });
}

/** Image/URL 打桩：blobToCanvas 的解码路径在 jsdom 直通。 */
function stubImagePipeline(): void {
  class FakeImage {
    onload: () => void = () => {};
    onerror: () => void = () => {};
    width = 100;
    height = 80;
    naturalWidth = 100;
    naturalHeight = 80;
    set src(_v: string) {
      setTimeout(() => this.onload(), 0);
    }
  }
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('URL', {
    createObjectURL: () => 'blob:fake',
    revokeObjectURL: () => {},
  });
}

describe('exportNarrativePdf', () => {
  it('逐章 capture → 多页 PDF 落盘（%PDF 产物 + 页序 + 章节标题）', async () => {
    stubImagePipeline();
    const chapters: NarrativePdfChapterInput[] = [
      { id: 'ch-0', title: '概览' },
      { id: 'ch-1', title: '热点分析' },
      { id: 'ch-2', title: '结论' },
    ];
    const captured: string[] = [];
    const progress: string[] = [];
    const blob = await exportNarrativePdf(
      chapters,
      async (ch) => {
        captured.push(ch.id);
        return fakePngBlob();
      },
      'GeoAgent 叙事导出',
      (p) => progress.push(`${p.current}/${p.total}:${p.chapterTitle}`),
    );

    expect(captured).toEqual(['ch-0', 'ch-1', 'ch-2']);
    expect(progress).toEqual(['1/3:概览', '2/3:热点分析', '3/3:结论']);
    // 落盘产物：Blob 且为 PDF 字节流
    expect(blob).toBeInstanceOf(Blob);
    const bytes = new TextDecoder().decode(await blob.arrayBuffer());
    expect(bytes.startsWith('%PDF-1.4')).toBe(true);
    // W9 页契约：pages[0] 为封面（第 1 章）→ 3 章共 3 图 2 次加页；
    // 每章标题由 exporter 栅格化进页画布（W9 设计），doc.text 只承载
    // 文档标题/副标题/页脚。
    expect(jsdocCalls.addImage).toBe(3);
    expect(jsdocCalls.addPage).toBe(2);
    expect(jsdocCalls.text).toEqual(expect.arrayContaining(['GeoAgent 叙事导出', '3 个章节']));
    vi.unstubAllGlobals();
  });

  it('空章节列表拒绝导出（诚实失败）', async () => {
    await expect(
      exportNarrativePdf([], async () => fakePngBlob(), 'x'),
    ).rejects.toThrow('没有可导出的章节');
  });
});
