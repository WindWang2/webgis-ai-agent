# Progress（真实执行记录）

| Wave | Commit | 验证 |
|---|---|---|
| W1 死码门+Sink | f3ee5dd4 | 56 passed |
| W2 schema | eefd0252 | 20 passed |
| W3 TS 投影 | 294fe0a2 | 25+2 passed, tsc/eslint 绿 |
| W4 render scene | 86ad29d6 | pytest 11 + vitest 7（双语言 parity 一次通过）|
| W5 legend 单源 | 8e52c2a8 | 前端全量 2749 passed；后端 cartography 998 passed |
| W6 label collision | f51ef3a8 | 差分 fixtures 双侧逐坐标一致；54 passed（gate+twin）|
| W7 publication chrome | 09ef130a | 1023 passed（含 report 回归 R1-Min2 清单）|
| W8 vector PDF | 412c554e + 77050ea1 | 1040 passed；pypdf 文本可提取断言通过 |
| W9 spec frames | 1b36f5d1 | vitest 5 passed；tsc/eslint 绿 |
| W10/W11 收尾 | dd608287 | 1029 passed |

环境修复（预存问题）：
- weasyprint 70.0 安装（report PDF 测试原先 skip → 运行后暴露 ObjStm 页对象
  正则失效 —— pypdf 结构化读取修复，pypdf 进 requirements-dev）
- worktree node_modules symlink 主仓

## Known Limitations（诚实登记）
- canvas 前端导出不映射 spec frames 的 layerOverrides/pageSize（MapLibre
  setFilter 语义差异；由后端 publication PDF 承接）—— spec-frames.ts 注释登记
- 矢量 PDF 栅格/瓦片层诚实省略（+披露）；dataUrl 嵌入为 follow-up
- curved textPath 标签 planned（本 Epic native = angled keep-upright）
- cartogram 保持 cartogram_unsupported 诚实降级；terrain 导出 live-only
- WeasyPrint 串行化是单进程内互斥；多进程部署需外部协调（登记 follow-up）
