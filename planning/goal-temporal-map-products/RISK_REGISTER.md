# RISK_REGISTER — GOAL 03

| 风险 | 等级 | 缓解 |
|---|---|---|
| #1837 cube transform 缺陷波及本任务 raster 路径 | 中 | 只做 x0=y0=0 全窗读；新代码自算 bbox；冲突矩阵登记 |
| map-panel.tsx 共享文件与他 PR 冲突 | 中 | 最小插槽式变更；每次提交前 fetch diff 复查 |
| atlas time driver 侵入 #1871 归属 | 中 | additive enum/字段/planner 分支；manifest 不改既有字段语义 |
| 时间表示误判（字符串 vs epoch） | 高 | bind 时 profiler 探测 + 表示固化进契约 + 编译器断言表示一致 |
| 播放网络风暴 | 高 | M7 cache/fingerprint/防抖/取消 + 指标测试 |
| 虚构期数（用户要 5 年数据只有 3 年） | 高 | frames 仅真实桶 + typed fallback + E2E-3 |
| 时间游标与图表面板静默错配 | 高 | 同 where fingerprint 断言进 E2E-1 |
