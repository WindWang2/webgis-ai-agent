# Risk Register — gis-analytical-foundation-vnext

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | 与 open PR #1965 冲突（scene3d/terrain/observation/UI） | 低 | 零文件交集已核实；默认只读交叉点（MapSpec/ToolDispatchService/map-panel/chat.py），确需写时单独 commit + 契约测试锁定 |
| R2 | 与 Review-R2 issue 修复线撞车（S96 network/S103 spatial_decision/S105 spatial_quality） | 中 | 只做增量能力（typed artifact/守门/oracle/局部重算），不修这些 issue 指认的具体 bug；网络链实现显式标注"拓扑正确性依赖上游 issue #1909"，oracle 数据避开 data_quality 环截断路径（#1844） |
| R3 | 重复合并底座（#1869/#1876/#1760/#1761 已实现大半语义） | 高 | B0 盘点先行：只实施确认 missing/partial 的增量；每项需求标注 implemented/partial/missing/in_other_PR |
| R4 | 同机多 agent 并行，构建资源争抢 | 中 | 全程 -j2；不杀他方进程；重型测试串行；先探测负载再升级 |
| R5 | 数据不可用导致"伪造统计结果" | 高 | 三条方法链全部 local fixture 驱动；真实分母缺失时必须 not_evaluated/missing_data 硬阻断（有测试证明不产图） |
| R6 | 契约破坏既有 MapSpec/ToolRegistry/Pi 面 | 高 | 全部 additive 字段 + feature flag（默认值明确）；versioned DTO；不改公开 JSON Schema 既有键语义 |
| R7 | partial 被当 final 渲染 | 高 | 状态机补 not_evaluated/stale 显式区分；MapProduct 侧断言（测不到则记 BLOCKED 并披露） |
| R8 | 性能实验拖垮本地机器 | 中 | 小规模 deterministic fixture；cpu/内存比较用轻量计时；基准测试纳入 tests/benchmarks/ 既有机制 |
| R9 | gh/网络不可用阻塞 PR | 低 | 已验证 gh 可用；若失败保存完整 PR body 与命令并如实报告 |

## 结论记录
- 2026-11-06：R1 核实为零交集；R4 起步 -j2（同机另一 agent 100% CPU 占用中）。
