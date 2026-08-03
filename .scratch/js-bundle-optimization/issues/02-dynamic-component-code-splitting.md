# 02 — 重型组件代码拆分与 Dynamic Import

**What to build:** 在 `/` 与 `/story` 页面对 `MapPanel` (WebGL Canvas) 及二次抽屉组件（`HistoryDrawer`, `SettingsPanel`, `RagIndependentPanel`, `ExportMask`）使用 `next/dynamic` (`ssr: false`) 进行按需异步拆包，使首屏 JS 大小由 1.15 MB 降低至 301 KB。

**Blocked by:** 01 — Next.js 模块级树摇（Tree-Shaking）优化

**Status:** ready-for-agent

- [x] 地图与二次抽屉组件通过 next/dynamic 异步懒加载
- [x] Zustand useHudStore 状态水化保持 100% 一致
- [x] 前端 Vitest 测试套件 61 个文件（409 个测试）100% 绿灯通过
