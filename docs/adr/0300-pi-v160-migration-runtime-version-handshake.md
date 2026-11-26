# 0300. Pi v1.6.0 迁移、构建入口修复与运行时版本握手

**Date:** 2026-11-25
**Status:** Proposed
**Branch:** `feat/pi-next-gis-host-20261124`
**Baselines:** ADR-0282（v1.5.0 兼容契约）· ADR-0288（v1.5.1 发现/嵌套/停机，C11–C16）· G22（v1.5.2 pin-only 裁决）· ADR-0286（三态披露）· ADR-0224（PiToolSpecV1/SurfaceReceipt）

## Context

两件事迫使本轮迁移：

1. **master 的 Pi 构建入口已损坏三周**。1b3f9e415（g46 merge，随 PR #2169 于
   2026-11-16 入 master）把 `vendor/pi` 从 submodule gitlink（v1.5.2 =
   `b4afdddf4`）换成了指向另一台机器 worktree 的**绝对路径符号链接**
   （`120000 blob dd645f502`）。后果：任何新 clone 的
   `scripts/build-vendor-pi.sh`、`pi_doctor`、Dockerfile 的 pi-builder 阶段
   全部失效；CI 因 rpc-entry 车道显式 skip（不静默绿）而未红。本 ADR 的前置
   修复 commit 把 gitlink 原样恢复到 v1.5.2，再在其上做升级——回退路径因此
   始终存在。
2. **上游 v1.6.0（`faac58588`，2026-11-20 stable）交付了 host 消费面的行为
   修复**。RPC wire 文档与 v1.5.2 **基本一致**（`rpc.md`/`json.md`/
   `rpc-extension-ui.md` 逐字节一致；`rpc-commands.md` 差 1 行纯文档——
   未消费命令 `get_last_assistant_text` 补记「跳过 abort/失败后无文本消息」，
   即 #12119，本机 diff 核实），agent-loop 与 rpc-mode 的行为变化直接
   落在本仓消费的契约上（C2/C6/C10 邻接）：
   - **#9986（agent 包）**：abort 时批量中未执行的工具不再静默缺结果——
     sequential/parallel 两路都移除了 abort break，`prepareToolCall` 在
     hooks 之前检查 aborted 并产出 "Operation aborted" error tool result。
     本仓 `PiBridge.abort` 的 CONC-F1 快照语义不变，但 vendor transcript 与
     事件流在 abort 后**每个 toolCallId 都有终态**——此前 mapper 收到的
     「无 start 无 cache 的 end」从未在真实路径出现，现在会。
   - **#10470（rpc-mode）**：`new_session` 等命令不再双触发扩展
     `session_start`；订阅先于 `bindExtensions`。本仓 `_isolate_vendor_session`
     依赖 `new_session`，扩展的 `session_start`（applyDefaultActiveTools）
     从「跑两次（幂等）」变为「跑一次」。
   - **#11264**：扩展 `pi.events` handler 异常在 RPC 模式发 `extension_error`
     事件（mapper 当前无 handler → 静默丢弃，见 §Consequences 的已知缺口）。
   - **#12119**：`get_last_assistant_text` 跳过 abort 后无文本消息（本仓未
     消费该 RPC，记录在案）。
   - **#12049/#12067**：schema 含无 type 裸节点 → OpenAI strict 静默降级
     non-strict；Anthropic non-strict schema 显式拒绝额外参数。
   - **retry**：socket 错误（ECONNRESET/EPIPE/ETIMEDOUT）与
     "Connection closed." 触发自动重试。
   - coding-agent breaking（扩展组件 `render()` 返回 `readonly string[]`）：
     本仓扩展 headless（无 render/Component 使用），不触及。

## Decision

1. **pin v1.5.2 → v1.6.0**（`faac58588`）。重建必须 `--reinstall`：上游把
   `openai` 依赖 7.19.0 → 7.59.0，旧 node_modules 导致 TS2339。回退 =
   gitlink 回 `b4afdddf4` + `build-vendor-pi.sh --reinstall`（floor
   `PI_MIN_VENDOR_TAG=v1.5.1` 不动，v1.5.2/v1.5.1 仍满足下限）。
2. **运行时版本握手走产物自报**。RPC `get_state` 无版本字段（v1.5.x/v1.6.0
   实测），host spawn 设 `PI_SKIP_VERSION_CHECK=1`——`node <rpc-entry>
   --version` 是唯一让「实际承载 wire 行为的字节」自报版本的通道。
   `pi_compat.probe_vendor_runtime_version()`（10s 上限、`(path, mtime_ns)`
   缓存、失败同缓存为 None）+ `attach_runtime_version()`（frozen replace）。
   `pi_vendor_identity()` **保持 stat-only**（cheap 路径纪律：不 spawn），
   探针由诊断出口 opt-in。
3. **pi_doctor vendor 域三方对账**：runtime probe vs describe vs superproject
   gitlink。probe≠describe =「checkout 已升级、产物旧构建（忘 rebuild）」→
   WARN "stale build"（wire 行为由产物决定，describe 不算数）；已构建但
   probe 不可得 → WARN（无法证明产物版本）；pin 漂移 WARN 合并 probe 信息。
4. **Host 侧能力表是推断不是协议**：`PI_VENDOR_CAPABILITIES`（4 位）+
   `vendor_capabilities()` 纯函数，供诊断/日志/未来 gate；RPC 无 capability
   协商，任何「已确认具备」的表述只能来自活体契约车道。未知版本 → 全 False
   并如实呈现为未知。
5. **v1.6.0 行为契约钉进车道**（红在 v1.5.2、绿在 v1.6.0）：
   - abort 批量终态（真实慢桥 fetch 中 abort；每个 toolcall_end id 有终态
     end + settled 恰一次 aborted:true）；
   - `new_session` 后工具面存活（#10470 的可观测面）；
   - mapper 对「无 start 无 cache 的 end」产出诚实 step_error（#9986 引入
     的新事件形状）。
6. **schema 严格模式卫生先披露后收敛**（口径经 review 收紧，见
   `tests/unit/test_pi_native_surface.py` 车道 docstring）：
   - 上游 #12049 的**增量谓词**（v1.6.0 `constrained-sampling.ts`
     `makeJsonSchemaNodeStrict`）是「节点无 `type` 且无 `anyOf` 且无
     `enum`」→ strict 静默降级；注释明示裸 `const` **不算** typed。
     `$ref`/`$defs`/`oneOf` 等 keyword 拒绝在 v1.5.2 已存在（非本轮增量），
     车道单独分桶披露。
   - **接入条件**：本仓扩展 `registerTool` 未设 `constrainedSampling`
     （vendor 该字段可选，未设 = 线上 `strict:false`）→ GIS 工具当前全部
     以非 strict 发送，#12049 降级路径**尚未被触发**；本车道是「预接入
     卫生」+ 存量债披露，不是现存缺陷的修复声称。
   - native 前门 7 工具中 `query_local_poi` 违规（`bbox`/`polygon` 无
     注解）→ xfail(strict=True) 钉住（修复注解会改变冻结 native 工具的
     参数校验面，超本轮授权；修好时 XPASS 翻红强制摘标）；长尾
     ~136/529 工具（#12049 裸节点 + v1.5.2 既有 keyword 节点两桶）→
     计数披露测试。修复注解是后续独立 pass，不是本轮 silent-green。

## Consequences

- **正向**：abort 后 transcript 自洽（每个调用有终态）→ 前端/回执不再需要
  「缺失 = 未执行」的隐式推断；session_start 单次；socket 级 provider 错误
  自动重试；构建入口从「仅本机 symlink 可用」恢复为任何 clone 可复现。
- **已知缺口（记录在案，非本轮 silent-green）**：
  1. `extension_error` RPC 事件在 `pi_event_mapper` 无 handler（返回 None
     静默丢弃）——本轮在契约上确认事件可达，结构化消费（WARNING 日志 +
     有界字段）列为后续最小增量。
  2. v1.6.0 rebind 后运行时诊断写 stderr（`takeUnreportedDiagnostics` 的
     RPC 面表现）：host stderr reader 只 DEBUG 记录。SDK-host API 本身不经
     RPC 可达，不消费。
  3. schema 裸节点长尾（134/529）待注解 pass。
  4. 契约车道历史 `TURN_PROMPT` 的 marker 是单 `\n`，与生产
     `attach_turn_context` 的 `\n\n` 形状不一致（扩展 routingSuffix 正则
     匹配不上 → turn token 为空）。既有测试不受影响故未统一改；abort-batch
     车道用生产形状常量 `BRIDGE_PROMPT` 表达。
- **证据**：pi_doctor 六域 no-FAIL（529 tools / native 7/7 / 活体 RPC 握手
  turn settle）；`test_pi_vendor_contract.py` 13 passed（11 基线 + 2 新
  conformance）；8 文件 Pi 批次 47 passed（44 基线 + 3 个 stale-test 修复，
  无新失败）；schema 卫生车道 native xfail(strict) + 长尾披露。全部在
  v1.6.0 pin 上实跑（见 PR 测试报告）。

## 升级核查清单执行记录（对照矩阵 §3）

- rpc*.md diff：`rpc.md`/`json.md`/`rpc-extension-ui.md` 逐字节一致；
  `rpc-commands.md` 差 1 行纯文档（未消费的 `get_last_assistant_text`
  行为描述，#12119）→ wire 兼容成立。
- 事件词表：既有 10 类不变；`extension_error` 在 RPC 模式新可达（见缺口 1）。
- 扩展 API：本仓消费的 4 hook 名 + `registerTool`/`setActiveTools`/
  `ctx.ui`/`ctx.sessionManager.getEntries()` 在 v1.6.0 types.ts 全部存在
  （L777/807/831/954、on() 重载 L1648-1663）。
- CLI：`--mode rpc --no-session --no-builtin-tools --extension` 不变。
- 依赖：根 lockfile 仍在（npm ci 路径 C16 保持）；`openai` 传递依赖大版本
  跳升 → `--reinstall` 重建纪律写入 Decision 1。
