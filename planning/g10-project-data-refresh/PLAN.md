# G10-PROJECT-DATA-REFRESH · PLAN（自管理目标循环）

执行模式：`/goal-loop` 未安装 → 回退自管理循环。每轮写 `LOOP_LEDGER.md`；完成定义 = Oracle 全绿 + 证据闭环，不是对话轮数或代码行数。

## 目标（一句话）

让用户更新学校点/行政统计表/栅格后，**知道**哪些分析/图层/统计受影响 → **先预览**变化 → **只重算可证明受影响部分**且保留人工制图调整 → **受控采纳或回退**；Pi 能解释「为什么更新、更新了什么、哪些不能自动合并」。

## 功能切片（每片一个可回滚 commit）

| 切片 | 内容 | 对应工作包 / Oracle | 依赖 |
|---|---|---|---|
| S0 | 账本 + 证据骨架 | M0 | — |
| S1 | 刷新契约 + 来源清单（逻辑身份/内容版本/证据级别/五态） | UPD-01 / O01 | S0 |
| S2 | 差异判定（字段/要素/几何/栅格/范围，四态披露） | UPD-02 / O02,O03 | S1 |
| S3 | 影响图 + 只读更新计划 + proposal fingerprint | UPD-03 / O03,O04,O05 | S2 |
| S4 | 隔离候选执行（绑定版本集、幂等、失败/取消/崩溃恢复） | UPD-05 / O05,O07 | S3 |
| S5 | user-wins 三方合并 + 原子 CAS 采纳 / 放弃 / 回退 / GC pin | UPD-04,UPD-05 / O06,O08,O09 | S4 |
| S6 | REST 端点 + 权限门 + 有界 schema | UPD-06 / O01,O05,O10 | S5 |
| S7 | Pi 自然语言工具（同一链、类型化回执、不伪造 ACK） | UPD-06 / O10,O11 | S6 |
| S8 | 前端面板 + API client + 双语 i18n + 键盘/加载/空/失败态 | UPD-06 / O10 | S6 |
| S9 | 三场景 fixtures + 增量/全量对拍 + 性能报告 | UPD-07 / O12 | S1–S7 |
| S10 | 独立 Review → Fix → Re-Review（第二轮新 HEAD） | M8 | 全部 |
| S11 | 同步 master + 最终验收 + push + PR（不合并） | M9 | S10 |

## 阶段映射（E）

| 阶段 | 覆盖切片 | 完成判据 |
|---|---|---|
| M0 取证 | S0 | BASELINE/OWNERSHIP 落地，PR 状态逐条核实 |
| M1 失败测试 | S1 前置 | 3 个 fixture 场景 + 反例测试先红 |
| M2 输入版本与差异契约 | S1,S2 | O01/O02/O03 单测绿 |
| M3 依赖影响预览 | S3 | O04/O05 预览无写入、绑定版本 |
| M4 隔离候选更新 | S4 | O05/O07 失败/取消/崩溃/幂等 |
| M5 三方合并/采纳/回退 | S5 | O06/O08/O09 |
| M6 项目 UI 与 Pi 工具 | S6,S7,S8 | O10/O11 |
| M7 性能/并发/取消/GC | S9 | O12 + 结果等价先于耗时 |
| M8 独立审查两轮 | S10 | 本分支引入 P0/P1 全部关闭 |
| M9 同步 master + PR | S11 | PR 创建，不 merge |

## 架构红线

- **不新建**第二执行器、第二 registry、第二 MapSpec、第二 session truth store。
- 复用权威：`fingerprints.classify_change` / `staleness_verdict` / `compute_reuse_fingerprint`、`artifact_registry.build_artifact_graph`、`data_lifecycle.service.impact_analysis`、`workflow_v4.recompute.compute_affected_subgraph`、`workflow_runtime.recompute_plan/apply_changes`、`mapspec.lifecycle_engine.apply_mutation`（`expected_revision` CAS）、`map_operations.commit_map_operation`、`project_runtime.resume` / `map_product_service`。
- 刷新状态只存 `app/services/project_runtime/refresh/store.py`（项目运行时体系内的领域账本，复用既有原子写 + fcntl lease 原语），**不是** session truth。
- 预览一律零写入；执行绑定 `source-version set + proposal_fingerprint + expected MapSpec revision` 三元组。
- 抽样差异只标 `sample/unknown`；预算不足 → `conservative full recompute` 或 `blocked`，**绝不 silently unchanged**。
- 全局分类断点 / 核密度带宽 / 网络拓扑 / 分母 / 行政边界变化 → 扩大依赖域或整项重算；标题/颜色改变 → 零 GIS 重算。

## 环境与资源

- 编译 `-j2`（上限 `-j4`）；`CMAKE_BUILD_PARALLEL_LEVEL=2`、`MAKEFLAGS=-j2`、`OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`。
- pytest 1–2 workers，禁用 `-n auto`；浏览器 1 worker。
- 独立端口/临时目录/测试 DB；重型任务用 git-common-dir 下共享 `flock` 做资源协调（不改业务锁）。
- 失败三轮必须更换诊断假设，不重试同一路径。

## 完成定义 / 停止条件

- 所有 MUST 旅程具备：可发现入口、真实服务调用、持久化/恢复、失败/取消处理、权限检查、用户反馈、正反测试、操作说明。
- Oracle O01–O12 全部有证据；关键确定性/权限/完整性验收在最终代码上重复两遍。
- 存在外部不可解阻塞时：保留可运行部分 + 证据，明确 `BLOCKED`，必要时建标注不完整的 Draft PR；**禁止把 BLOCKED 写成全部完成**。
