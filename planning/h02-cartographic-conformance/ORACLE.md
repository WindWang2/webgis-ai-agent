# H02 ORACLE — 验收判据（全部可检查）

1. ✅ 每个修改点有现存生产调用方；单一 MapSpec/registry/ABI/RenderWork 权威不破坏。
2. 29 个活跃组合模板登记/组件能力/必需 slots/导出支持自动检查；**≥18 正例 + ≥12 负例**，双语言/多尺度/不完整数据/用户 durable 编辑有合约与可重放证据。
3. 页面地图/导出对「数据 revision、图层显隐、数据过滤、专题分类字段、色彩分级、legend、来源/时间、组件必需性」机器可验证语义一致；unsupported 有披露。
4. 版式越界/碰撞/标题截断与用户 pin 优先权有自动化测试；解决不了 fail-honest，不自动挪用户锁定组件。
5. L1–L5 QA、product completeness、RenderWork ACK 与旧代拒收门维持；issue 交叠矩阵在 RECON/CHANGE_MAP，不重复抢单。
6. pytest cartography lane 与受影响 unit/integration 全绿（附退出码；存量红基线实证披露 BASELINE.md）；前端 typecheck/lint/定向 vitest 过关附退出码；不 weaken 任何测试。
7. 内存/构建耗时/关键制图质量基准客观报告（PERF.md）；fixture 不依赖商业在线 API/LLM key。
8. 无 GB 级数据进 git/上下文；bounded ref descriptor；权限/组织/session 守卫保留；schema 版本遵原仓漂移检查。
9. 独立 reviewer ≥1 次完整 Diff review + 高风险链路对抗复现；新引入 P0/P1/P2 全修复复审；核心 Oracle 连续两次通过。
10. `git diff --check` 干净；终态再 fetch 最新 master 补交叠判定；RECON/CHANGE_MAP/ORACLE/REVIEW/PERF/WORKLOG 齐备。
11. `gh pr create` 成功返回 PR URL（不 merge、不等线上 CI）；PR body 按 docs/dev/pr-body-template.md。
