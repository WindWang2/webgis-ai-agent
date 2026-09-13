# ac-08 导出样例（docs/dev/ac-08-samples/）

随 PR 入档的导出质量取证产物。全部可由仓内探针复现。

## dpi-baseline/（P0 基线 + P7 门禁取证）

`dpi-line-probe.mjs` 输出：同一 MapSpec（递减线宽行组）在
96/150/300 DPI 下「真重渲染」与「放大插值」两路径的对比度与有效线宽
（`dpi-baseline.md` / `dpi-baseline.json` + 各路径 PNG）。

- 300 DPI 0.5px 线：真重渲染对比度 0.948 / 有效线宽 0.32px；
  放大插值 0.253 / 2.24px（模糊膨胀 ~4.5×）—— §5 门禁「真重渲染优于
  放大基线」的数值依据。
- 复现：`cd frontend && node scripts/ac08/dpi-line-probe.mjs --dpi 96,150,300 --out ../docs/dev/ac-08-samples/dpi-baseline`

## sample-export-cjk.pdf（P3 中文文本层样例）

`pdf-text-probe.mjs` 用真实 jsPDF + 仓内 vendored Noto Sans SC 子集生成：
CJK 标题/副标题/页脚为真文本层（Type0 + FontFile2 嵌入）+ 位图地图体。
`tests/cartography/test_export_sample_pdf_text.py` 以 pypdf 断言中文可提取、
字体已嵌入（禁止肉眼验收的硬门禁消费本样例）。

- 复现：`cd frontend && node scripts/ac08/pdf-text-probe.mjs --out ../docs/dev/ac-08-samples`
