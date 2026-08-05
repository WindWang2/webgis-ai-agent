# 03 — 客户端 PDF 导出器按需延迟加载

**What to build:** 在地图与报告导出模块中使用 `await import('jspdf')` 按需动态加载 `jsPDF` 依赖，将 300KB 的 PDF 渲染逻辑剥离出首屏下载。

**Blocked by:** 01 — Next.js 模块级树摇（Tree-Shaking）优化

**Status:** done — verified 2026-08-05 (code + tests)

- [x] jsPDF 使用动态 await import('jspdf') 异步加载
- [x] 地图与报告 PDF 导出功能正常运行
