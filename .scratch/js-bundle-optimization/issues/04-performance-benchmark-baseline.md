# 04 — 性能防回归基线与 Benchmark 自动化存档

**What to build:** 测量优化后页面的 FCP/LCP 与 Bundle 大小，将基线数据存档至 `.gstack/benchmark-reports/baselines/baseline.json`，确保评分从 🟡 B 升至 🟢 A+。

**Blocked by:** 02 — 重型组件代码拆分与 Dynamic Import, 03 — 客户端 PDF 导出器按需延迟加载

**Status:** ready-for-agent

- [x] .gstack/benchmark-reports/baselines/baseline.json 保存存盘
- [x] /benchmark 检测通过并评级为 Grade A (A+)
