# RISKS — goal-04-carto-pub

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | 与 #1965 合并冲突（quality_loop.py + 3 测试 + golden 再生） | 高/中 | 四文件全程只读；D4 增量落 selfheal_actions/semantic_checks/新模块；PR 前重新 fetch，如 #1965 已合并则以 rebase 处理只读文件无冲突 |
| R2 | 重做已合并能力（#1870/#1871/#1875/#1880） | 中/高 | WBS 只列缺口；每工作包开工前对照 PR_OVERLAP_MATRIX B 表核对；实现中发现已覆盖即收缩并记录 |
| R3 | MapSpec schema 变更触碰跨域契约红线 | 中/高 | 约束 DSL 优先做独立模块 LayoutConstraintSpec（组件描述符/placement 的旁路可序列化投影）；确需 schema 字段时最小 additive + 默认值兼容 + 双侧类型测试 + 单独 commit |
| R4 | 双实现漂移（Python/TS 镜像：layout-description、label solver、约束求解） | 中/高 | 沿用共享 golden corpus 跨树对拍模式；新增约束/披露一律双侧同 move + golden 同步再生成 |
| R5 | Chromium/Playwright 不可用 → D5 视觉 E2E 无法真实执行 | 中/中 | 先探测本机 playwright/chromium 可用性；不可用则结构 golden + 单元证据，PR 中如实 NOT_RUN + 阻碍说明，绝不声称视觉通过 |
| R6 | 前端 worktree 无 node_modules，pnpm install 重 | 高/低 | 检查 pnpm store 共享；install 一次后增量；全量 vitest 只在里程碑跑（-j2 约束），平时定向文件 |
| R7 | 金标/基线既有失败被误当回归 | 中/中 | R1 先在干净基线跑目标测试面记录 before 状态；每轮对比新增失败归因 |
| R8 | 同机多 agent 并行资源争抢 | 高/中 | -j2、不并发重型构建；pytest 用定向路径；pnpm build 仅必要时一次 |
| R9 | 破坏既有 golden（layout_solver V2/V3 回归锁定、standards_cases 230 例） | 中/高 | 求解器扩展走 V5/新函数不动 V2–V4 行为；输出投影兼容；改默认值必须 golden 再生成并逐一 diff 审 |
| R10 | #1910 修复合入时间不定 → W3.3 验收测试悬挂 | 中/低 | W3.3 测试写成"前端侧不变式"（字体装载/降级码语义），不依赖后端修复时序；后端回执一致性核对标注 blocked_on_#1910 的 skip 理由（self-skip 语义） |
| R11 | issue 认领文件被误改（component_composer S14-003/013） | 低/中 | 尽量零改动该文件；若必须，diff 逐行对照 #1966/#1967 点名行号避开 |
| R12 | 任务书"3 亿 tokens 预算"被误读为必须穷尽 | 低/中 | 以验收证据完成为准；ledger 记录实际消耗量级即可 |
