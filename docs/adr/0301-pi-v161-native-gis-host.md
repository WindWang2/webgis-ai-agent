# 0301. Pi v1.6.1 升级与可复现原生 GIS Agent Host：供应链修复、能力快照与运行治理

**Date:** 2026-11-26
**Status:** Proposed
**Branch:** `zcode/goal-pi-native-gis-host-20261125`
**Baselines:** ADR-0282（v1.5.0 兼容契约）· ADR-0288（v1.5.1 原生发现 C11–C16）· ADR-0286（TurnPhase/三态披露）· ADR-0231（wrap vendored Pi）· ADR-0103（动态工具表面）· ADR-0224（PiToolSpecV1/SurfaceReceipt）

## Context

master（`8fe3d7663`，2026-11-20）上 `vendor/pi` 的 git 对象形态是 **mode 120000 的
symlink blob `dd645f502`**，指向本机绝对路径
`/home/kevin/projects/webgis/webgis-worktrees/g48-gcp-georeferencing-20261115T125056Z/vendor/pi`
（该工作树已删除，符号链接悬空）。回归点已考古闭环：merge commit `1b3f9e415`
（2026-11-16，PR #2169 合并 g46 时）的冲突解决把本地 checkout 的 symlink 误加入
提交，替换了此前 c93a58b31 记录的 v1.5.2 gitlink。后果：fresh clone 的
`build-vendor-pi.sh`（要求已初始化子模块）直接退出 2，`pi_doctor` 全检不可信，
Dockerfile/Dockerfile.prod 的 pi-builder stage 与 CI 的
`git submodule update --init vendor/pi` 全部失效；同时 `pi_compat._expected_pin`
读取 `git ls-tree` 的 mode/type/SHA 三元组，symlink 下降级为 `unknown`——pin 漂移
检测静默失效（WARN 都发不出）。

上游 Pi 正式 release：**v1.6.1**（2026-11-25T13:41:35Z published，lightweight tag
→ commit `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`，与 stable head 同 commit；
tag 独立存在已用 `git ls-remote` 双查询验证）。相对 pin v1.5.2 的协议/能力增量：

- **v1.6.0**（`faac5858`，2026-11-20）：RPC `new_session`/`switch_session`/`fork`
  的扩展 `session_start` 双跑修复 + `entry_appended` 事件向 RPC 客户端转发；
  `AgentSessionRuntime.takeUnreportedDiagnostics()`；Esc 中止批量工具调用时未启动
  的调用获得 "Operation aborted" 终态结果；extension 组件 `render()` 返回
  `readonly string[]`（扩展侧 breaking）；MCP/`tool_search` resume 修复。
- **v1.6.1**（`ef4a1cfc`，2026-11-25）：`after_provider_response` 扩及 HTTP 4xx/5xx
  失败与每次重试尝试（含 `retry-after`）；codemode `models.classify()` 决策模型 +
  `responseId`；`notices.*` 设置替换 `showCacheMissNotices`（**设置面 breaking**，
  本仓库不写该键 → 无影响，矩阵记录）；RPC 行扫描 O(n²) 修复（图片等超大行）；
  `full_output_error`；extension dialog `blockedKind`；大量 provider 重试覆盖修复。

同方向先行分支 `feat/pi-next-gis-host-20261124`（本地、未推送、无 PR）已完成
symlink 修复 + **v1.6.0** pin + ADR-0300 + C17–C20。本任务按新授权（v1.6.1+、
可复现运行、动态工具、会话与治理全量 WP）从 master 独立实现，不 cherry-pick 该
分支；两者在 `vendor/pi`、`pi_compat.py`、`PI_UPSTREAM_COMPAT_MATRIX.md`、
`pi_doctor.py`、`test_pi_vendor_contract.py` 上必然语义重叠，合并顺序建议见
本 ADR 末节。ADR 编号说明：0300 已被 4 个开放 PR（#2272/#2273/#2275）与上述
先行分支并行占用（5 处撞号），本 ADR 取 **0301** 避免第 6 处。

## Decision

1. **恢复 canonical gitlink 并升 pin 到 v1.6.1**（`ef4a1cfc`，mode 160000）。
   `.gitmodules` 不变（URL `https://github.com/earendil-works/pi.git`）。不引入
   绝对 symlink、不提交 node_modules、不做未审计 vendor patch。协议 floor
   `PI_MIN_VENDOR_TAG` **保持 v1.5.1**（floor = 本仓库桥协议可接受的下限；pin =
   本仓库随附并验证的版本，两者解耦——floor 不盲随 pin 上移，旧 pin 仍可经 dev
   override 使用）。
2. **身份/能力判定从「盲版本号」升级为 typed 能力快照**（`pi_compat.py`）：
   - `PiVendorIdentity` 增量字段：`tree_mode`（`160000|120000|...`，git 树形态
     实测）、`observed_version`（`node <rpc-entry> --version` 真探针，来源
     `entry_probe`）、`entry_sha256`（入口文件内容指纹，short 形式）。
   - 新增 `PiCapabilitySnapshot`：封闭词表 `PiCapability`（tool_search/codemode/
     nested_tool_events/ordered_shutdown/prompt_disposition/
     compaction_events/auto_retry_events/provider_failure_events(v1.6.1)/
     extension_error_events(v1.6.0)/abort_batch_terminal(v1.6.0)/
     rpc_session_lifecycle_fix(v1.6.0)/unreported_diagnostics(v1.6.0)/
     decision_model_classify(v1.6.1)/rpc_line_scan_fix(v1.6.1)），静态
     feature→min-version 表为唯一映射点，观测版本 ≥ min 判 supported；
     每项附 evidence（`entry_probe>=vX.Y.Z` / `describe>=vX.Y.Z` /
     `observed<vX.Y.Z` / `unknown`——与代码实现字面一致）。
     消费方一律查 `caps.has(...)`，禁止在业务代码里散落版本号比较。
   - symlink 形态显式诊断：`tree_mode != 160000` 时 identity 给出
     `vendor_tree_not_gitlink` 有界原因码，doctor FAIL（不再是静默 unknown）。
3. **可复现构建与构建清单**：`build-vendor-pi.sh` 在成功后写
   `vendor/pi/.build-manifest.json`（pin SHA、describe、node 版本、npm lockfile
   hash、入口 sha256、构建时间戳、构建器版本），幂等（内容不变不重写）；
   `pi_compat`/`pi_doctor` 启动时对账 manifest vs 实际（pin 漂移、入口被替换、
   manifest 缺失 → 各自有界原因码）。**manifest 不进 git**：它写在子模块工作树
   内，超项目索引只追踪 gitlink SHA，天然不可见（无需 .gitignore 改动）；它是
   本机构建事实，不是仓库事实；仓库事实 = gitlink SHA。
4. **dev override（仅显式 opt-in）**：`PI_VENDOR_DIR` 环境变量允许开发者把
   vendor 指向另一 checkout（identity/doctor 均标注 `source=env_override`）。
   生产镜像与 CI 不设置该变量；未设置时行为与今天完全一致。override 下
   `matches_expected_pin` 语义变为「override checkout HEAD vs superproject
   gitlink」，漂移照常 WARN。
5. **v1.6.x 协议 delta 消费**（WP2）：
   - `extension_error` 事件有界转发到诊断（不假设 success，不泄漏内部文本）。
   - abort 批量工具调用的未启动项终态结果按普通 `tool_execution_end`
     （`isError` 语义）处理——桥现有事件映射天然覆盖，契约测试钉住。
   - `entry_appended` 转发与 `session_start` 单跑：桥无需行为变化（事件映射
     已按封闭词表处理未知事件为有界记录），契约车道升级为 v1.6.1 现场断言。
   - `after_provider_response` 失败/重试事件（WP5）→ 现有 typed usage/diagnostic
     ledger 加性记录 provider HTTP 状态与 retry-after，不记录请求全文/密钥。
   - `notices.*` 设置 breaking：本仓库 settings.json 投影不含
     `showCacheMissNotices`，无需迁移；矩阵记录。
   - 未在快照中的能力（如 chord/p3 设计）不视为已发布 RPC 功能，不消费。
6. **全量 WP 治理挂点不变**：工具执行权威仍 ToolRegistry/ToolDispatchService，
   MapSpec 写入仍 CAS lifecycle engine，模型账本仍 G37 ledger，mission/durable
   job 仍独立于 Pi turn。Pi session/branch 仅映射为运行历史。

## Consequences

- fresh clone 恢复「clone → submodule update → build → doctor 全绿」的可复现
  路径；CI 与 Docker 构建恢复对 vendor/pi 的真实引用（不再吞 symlink）。
- pin 漂移/入口替换/子模块形态错误从「静默 unknown」变为有界原因码 + FAIL，
  同类回归不再漏网。
- v1.6.1 的 provider 失败/重试可观测性进入 typed ledger，为 retry budget 与
  no-progress 治理提供权威证据（WP5）。
- 回退路径：gitlink 回 v1.5.2（`b4afdddf`）+ `scripts/build-vendor-pi.sh
  --reinstall` + rebuild；本 ADR 全部新增行为有 env kill-switch 或纯加性
  （快照字段/manifest/诊断码），回退不需改业务代码。
- 与先行分支 `feat/pi-next-gis-host-20261124`（v1.6.0）合并顺序建议：本分支
  先合（v1.6.1 是 v1.6.0 的严格超集，能力快照为 feature-based 可覆盖
  C17–C20 场景）；先行分支如需保留，应 rebase 到本分支并把 ADR-0300/C17–C20
  归并进矩阵，保留其独立测试资产。两者不得同时以现状合并。

## Verification（对应本仓库验收）

- clean clone 复现记录 + `pi_doctor` 真探测全绿（vendor/build/extension/surface/
  rpc/env 六检）。
- 契约车道 `tests/integration/test_pi_vendor_contract.py` 在 v1.6.1 现场
  （真 rpc-entry + 本地 stub LLM）通过；能力快照与 doctor 三方对账测试。
- symlink 回归负例：构造 120000 形态 → identity 原因码 + doctor FAIL。
