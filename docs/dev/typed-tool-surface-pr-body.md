# PR body 草稿（typed-tool-surface / ADR-0180）

> 最终 body 以本文件为准（review 结论回填后提交）。

## Summary

方向 4（Pi Typed Dynamic Tool Surface）落地。Phase 0 对账结论：任务书假设的"master 只有万能 webgis_execute"已被 ADR-0103（#1150）/0104/0119 系列超越——spawn dump v2 已把 316 个 registry 工具以真实 schema 注册进 Pi 扩展、per-turn `WEBGIS_ACTIVE_TOOLS`→`setActiveTools` 动态面、V3 选择器（reasons/dropped/confidence/abstain）、proxy wrap-reject 均已在产。本 PR 不造第二套动态面，只补三个真实缺口（T2/T4+T7/T9）+ T8 parity 钉：

1. **per-turn 激活面 schema 字节预算**（ADR-0180 D2）：`apply_surface_byte_budget` 按 `registry.schema_size`（#1062 缓存）贪心装入动态候选；native7 前门 + proxy 常量恒保留（前门不可失）；默认 32KB（`PI_SURFACE_BYTE_BUDGET`，0=off）；裁剪记因 `byte_budget` 进 disclosure + `Stage.TOOL_SURFACE` 链发射新字段（surface_bytes/surface_budget/budget_dropped）。spawn dump 不动（dormant 不压缩——单一真相纪律）。
2. **Pi 边界 pre-dispatch 输入闸**（D3/D4）：`pi_input_gate.validate_pi_tool_arguments` 在 dedup/wave 排队/ref 解析**之前**做零误拒分层校验——同一归一化声明表 → unknown-field（#828 同语义含 extra=allow 豁免）→ required → 容器-标量结构错位（解析只换字符串叶、绝不把容器变标量）→ 无字符串叶子树的 per-field TypeAdapter 探针（复用 registry #1113 P3-3 同一 helper）→ 全树无字符串叶时升级为与 dispatch 同一 `model_validate` → oversized 与 registry #699 同款旁路 → 闸自身故障 fail-open。拒绝返回 `SCHEMA_VALIDATION_REJECTED`（field-path issues + 修复证据 + retryable），不再伪装成工具业务失败（不占 dedup、不进 wave 队列、无 tool_failed 事件/harness_failure 分类）；同时保持 v3 Phase E 计划契约——`apply_tool_result(success=False)` best-effort 记账，能力行仍标 failed（可重试），由既有契约测试钉住。`ToolRegistry.args_model()` 新公开只读访问器：闸与 dispatch 共享同一 Pydantic 模型对象（语义零漂移的唯一途径）。
3. **面指标 + 回归门**（D5）：`pi_surface_metrics` 进程级计数（invalid_tool_name / schema_validation_rejected / proxy_wrapped / direct_surface）+ 最近面投影快照 + 有界拒绝样本；`/api/v1/metrics/digest` additive `pi_surface` 段（admin 门不变）。回归门：golden 面质量（必达工具/tier-3 零泄漏/预算内）+ gate 有界时延（快速拒绝证明非法调用不进 wave 排队）。
4. **parity（T8）**：gate 拒 ⇒ registry VALIDATION_ERROR（真实模型双向断言）；registry 拒 ⇒ gate 同拒或设计内字符串叶保守放行；裸名直调与 `webgis_execute(inner)` 解析出同一 (tool, args)；tier-3 双路不可达；dump 参数键面 ⊆ args_model 字段（单一 schema 真相）。

## 基线与 Phase 0 复核

- 执行时基线：origin/master `580b33e9`（Merge PR #1272）；创建分支后 master 未再移动（已 re-merge 验证 up-to-date）。
- T0 probe（真实 registry 实测）：327 registry 工具；注册超集 316；dump ~191KB；schema bytes max 6527 / median 528 / mean 604；native7 恒激活面 17,277B。
- 与 open/recent PR 防重复：唯一 open PR #1270（CI hygiene，mapspec/quality-manifest 面）与本线零文件交集，未吞入；#1271/#1272（AC-V11/ads-v1）为 cartography/data 域，零交集；无并行分支在 Pi tools 车道（remote branch 对账）。任务书 T1/T3/T5/T6 已由 master 既有实现覆盖（recon §3 重叠矩阵），本 PR 未重复施工。

## 架构 before/after

before（工具调用执行段）：
resolve/classify → 存在性 → tier≥3 拒 → ToolDispatchService.dispatch（dedup → reuse → wave 排队 → registry 校验[深藏] → 执行）→ 失败伪装成业务 result。

after：
resolve/classify → 存在性 → tier≥3 拒 → **input gate（归一化→结构→探针→升级档；fail-open）** →（拒：typed SCHEMA_VALIDATION_REJECTED + 计划失败记账，早退）→ ToolDispatchService.dispatch（原样）→ 执行。
per-turn 面：V3 select → 名单 → **apply_surface_byte_budget（32KB 默认）** → setActiveTools marker；disclosure/链发射/metrics 三观测面。

## 测试证据（本地，未等待线上 CI）

- 新增 32 tests（4 文件）全绿：budget 8 / input-gate 12 / metrics+gate 6 / parity 6。
- 受影响面回归全绿：pi_* unit 112 + pi bridge/concurrency/auth 111 + dispatch/normalization 49 + root pi（session-plan host/status fail-closed/runtime observability）34 + pi integration 25 + metrics API 2 + API compatibility contract 12 = 345。
- ruff（CI 同口径 `ruff check app/ tests/`）对全部改动文件 0 finding。bandit 本地未安装；改动无 SQL/exec/密钥面（ADR-0180 代码审阅可证），线上 security gate 不受本线影响。
- `git diff origin/master...HEAD` 文件面核对：16 文件（6 app + 6 docs + 4 tests），无无关文件、无 frontend 文件 → 按资源纪律跳过 `next build`（无 TS/前端面可编译变化）。

## master 预存失败对照

- 期间唯一本地失败为 `test_failed_dispatch_marks_rows_and_retry_recovers`——**本线引入的契约冲突**（闸早退跳过计划失败记账），已修复（84bf25e6）并由该既有测试继续钉住。
- master Backend 已知红（PR #1270 记录：8 failed / 16365 passed，quality artifact staleness 等线）集中在 quality manifest/generated-artifacts 域，与本线文件面零交集；本线未触碰 `tests/conftest.py`、`docs/quality/*`，无掩盖。

## 独立 review findings 与修复

（回填中）

## 兼容性 / 风险 / 回滚

- 全部 additive + kill-switch：`PI_SURFACE_BYTE_BUDGET=0` 回归纯 k_max 行为；闸 fail-open 时基线行为不变；`PI_DYNAMIC_TOOL_SURFACE`/`PI_SURFACE_METRICS` 语义不变。
- 扩展 .mjs/.ts 零改动（marker 协议与 MAX_ACTIVE=48 不变）→ 无 #694 双写漂移面；无 Alembic；无前端；ToolDispatchService 核心语义零改动（ADR-0068 管线原样）。
- 回滚面：revert 5 个 feature commit 即可完全回到 580b33e9 行为；无数据/生成物迁移。

## 后续接口点

- `native_surface_snapshot`（V2 投影栈）与预算闸的进一步统一；
- selection reasons 面向模型披露的 token 成本权衡（当前仅观测面）；
- pi_surface 段接入告警阈值（invalid-name/validation 率突增）。

## 声明

未等待线上 CI；未自动合并 PR。
