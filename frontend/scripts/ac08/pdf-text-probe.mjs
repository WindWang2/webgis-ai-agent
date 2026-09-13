/**
 * ac-08（ADR-0157 P3/P7）· PDF 中文文本层实证探针。
 *
 * 用真实 jsPDF + 真实 vendored Noto Sans SC 子集字体生成样例 PDF
 *（标题为 CJK 真文本层 + 位图地图体），落盘 docs/dev/ac-08-samples/；
 * 后端 tests/cartography/test_export_sample_pdf_text.py 以 pypdf 抽取
 * 断言中文可提取可检索（禁止肉眼验收的硬性门禁）。
 *
 * 运行（frontend/ 下）：
 *   node scripts/ac08/pdf-text-probe.mjs --out ../../docs/dev/ac-08-samples
 * 资源：单页、小尺寸（320×220 画布）、串行一次 —— 符合 §0.4 纪律。
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');
const JSPDF_JS = path.join(FRONTEND_ROOT, 'node_modules', 'jspdf', 'dist', 'jspdf.umd.min.js');
const FONT_TTF = path.join(FRONTEND_ROOT, 'public', 'fonts', 'NotoSansSC-Regular-subset.ttf');

const TITLE = '成都市学校分布图';
const SUBTITLE = '出版级导出 V10 · ADR-0157 中文文本层样例';
const FOOTER = '制图日期 2026-09-13 · WebGIS AI Agent';

const html = `<!doctype html>
<html><head><meta charset="utf-8"><script src="file:///${JSPDF_JS.replace(/\\/g, '/')}"></script></head>
<body><script>
window.__makePdf = async function (fontB64, mapDataUrl) {
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
  doc.addFileToVFS('NotoSansSC-Regular-subset.ttf', fontB64);
  doc.addFont('NotoSansSC-Regular-subset.ttf', 'NotoSansSC', 'normal');
  doc.setFont('NotoSansSC', 'normal');
  // CJK 真实文本层（可选取/可检索）—— 栅格化兜底路径不再参与
  doc.setFontSize(16);
  doc.setTextColor(30, 41, 59);
  doc.text(${JSON.stringify(TITLE)}, 148.5, 15, { align: 'center' });
  doc.setFontSize(10);
  doc.setTextColor(100, 116, 139);
  doc.text(${JSON.stringify(SUBTITLE)}, 148.5, 22, { align: 'center' });
  // 地图体（位图画布嵌入 —— canvas 链语义）
  doc.addImage(mapDataUrl, 'PNG', 40, 30, 217, 140);
  doc.setFontSize(7);
  doc.setTextColor(148, 163, 184);
  doc.text(${JSON.stringify(FOOTER)}, 148.5, 200, { align: 'center' });
  return doc.output('datauristring');
};
</script></body></html>`;

const browser = await chromium.launch({ args: ['--use-angle=swiftshader'] });
try {
  const htmlPath = path.join(FRONTEND_ROOT, '.ac08-pdf-probe.html');
  fs.writeFileSync(htmlPath, html);
  const fontB64 = fs.readFileSync(FONT_TTF).toString('base64');

  const page = await browser.newPage();
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'));

  // 画布地图体（离屏 canvas 绘制简单专题面）
  const mapDataUrl = await page.evaluate(() => {
    const c = document.createElement('canvas');
    c.width = 320;
    c.height = 220;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, 320, 220);
    ctx.fillStyle = '#2563eb';
    ctx.beginPath();
    ctx.moveTo(40, 180);
    ctx.lineTo(140, 60);
    ctx.lineTo(240, 180);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#de2d26';
    ctx.beginPath();
    ctx.arc(160, 110, 8, 0, Math.PI * 2);
    ctx.fill();
    return c.toDataURL('image/png');
  });

  const dataUrl = await page.evaluate(
    async ({ fontB64, mapDataUrl }) => window.__makePdf(fontB64, mapDataUrl),
    { fontB64, mapDataUrl },
  );

  const outIdx = process.argv.indexOf('--out');
  // --out 相对 frontend/ 解析（../../docs/dev/ac-08-samples = 仓库 docs）
  const outDir = path.resolve(
    FRONTEND_ROOT,
    outIdx >= 0 ? process.argv[outIdx + 1] : path.join('..', 'docs', 'dev', 'ac-08-samples'),
  );
  fs.mkdirSync(outDir, { recursive: true });
  const outFile = path.join(outDir, 'sample-export-cjk.pdf');
  fs.writeFileSync(outFile, Buffer.from(dataUrl.slice(dataUrl.indexOf(',') + 1), 'base64'));

  const size = fs.statSync(outFile).size;
  console.log(`[pdf-text-probe] wrote ${outFile} (${(size / 1024).toFixed(0)} KB)`);
  console.log(`[pdf-text-probe] text-layer strings: ${TITLE} / ${SUBTITLE} / ${FOOTER}`);
  fs.rmSync(htmlPath, { force: true });
} finally {
  await browser.close();
}
