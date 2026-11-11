# PLAN — G12-PI-NATIVE-INTEGRATION

目标：把 Pi-only 对话路径升级为真实使用最新稳定 Pi（本任务核定为 v1.5.1，见 W01 决策）的
WebGIS 宿主：原生工具发现（tool_search/deferred）、受控 codemode/嵌套 GIS 调用、生命周期与
长任务恢复集成；模型配置真实生效；全程回到现有单一权威链（ToolRegistry/dispatcher/MapSpec）。

## 版本决策（W01，已核）

**Pin = v1.5.1（`dd30101cfc5b0609411dd80b6f93adbc14db14cf`）**

- 任务书快照与标题均锚定 v1.5.1（"已核"）。执行时复查发现 v1.5.2（`b4afdddf`，发布于
  2026-11-10 13:01 +0100，晚于快照 ~5h、早于本任务启动仅 ~1.5h）。
- 对照结论（`git diff v1.5.1..v1.5.2 --stat` 逐目录核实）：coding-agent 的 RPC/扩展/工具发现/
  嵌套调用/停机代码在两 tag 间**零差异**（仅 anthropic 版本串、missing-cwd fork UX、失败导入
  清理、docs/lockfile）。v1.5.2 无本任务所需能力，且无浸泡时间。
- 结论：按任务书「已核 v1.5.1」锚定 pin；compat matrix 记录 v1.5.2 已评估为兼容面等价、
  后续升级为 pin-only 变更。Node 下限不变（>=22.19.0）。jiti 仍 2.7.0（Docker/frontend pin 不动）。
- 回退边界：gitlink 回 v1.5.0 + rebuild；本任务所有新行为均有 env kill-switch（见各包）。

## 关键 vendor 事实（全部在 v1.5.1 tag 亲自核实）

1. `tool_search`：BM25（Intl.Segmenter 分词 → 中文可用；$ref/$defs 解析进可检索文本 #11803）；
   只搜 `exposure ∈ {codemode, deferred}` 且未激活的工具；命中后 **merge** 进 active 集
   （`setActiveTools([...active, ...matches])`）→ 前门不会被打发现丢掉。
2. `codemode`：QuickJS 沙箱（无 Node/fs/network/timer），只能经 `ctx.executeTool` 调注册工具；
   预算 MAX_CONCURRENT=4、内存 256MiB、workers 2、输出 10k tokens、NESTED_CALL_LIMITS
   (256 calls/8KB per-call args/32KB total/500 err chars)。
3. 嵌套调用：`tool_execution_start/update/end` 事件带 `parentToolCallId`（id 形如 `<callerId>/<n>`），
   随 RPC 事件流转发（agent-session.ts:202-206 已核）；结果归父工具的 `nestedCalls` 记录。
4. `--no-builtin-tools` 只清默认工具选择（sdk.ts:282 defaultToolNames=[]），内置扩展
   （tool-search/codemode/mcp）仍加载，注册 `defaultActive:false` + `exposure:"model-only"`；
   由宿主 `setActiveTools` 激活。`-ne/--no-extensions` 才卸载。
5. 无 `{type:"reload"}` RPC。会话/配置代际更替 = 进程换代（respawn）或 set_model（仅模型）。
6. v1.5.1 RPC 新增语义：`clear_queue`（Esc 语义=先清队列再 abort）；abort 等待 idle 后响应；
   停机：首 SIGTERM 有序清理（dispose 等 tool settlement + credential writes，可数秒），
   第二信号立即强退；stdin EOF 后信号不中断清理。
7. v1.5.1 有根 package-lock.json（v1.5.0 无）→ build-vendor 可走 `npm ci` 提升可复现性。
8. v1.5.1 系统 prompt 开场白与 index.mjs CODING_ASSISTANT_OPENINGS[1] 匹配（已核 system-prompt.ts:156）。
9. set_model：provider/modelId 必须已在 Pi 注册（models.json）；无 RPC 级配置 reload。

## 实施波次（每波 = 读调用链 → 增量 → 真实验证 → 账本 → commit）

### Wave 1 — W01+W02 版本基线（先行纵切：pin+build+契约全绿）
- vendor/pi gitlink → v1.5.1；子模块内容 = 官方 tag 原样（不打补丁）。
- `scripts/build-vendor-pi.sh`：v1.5.1 有根 lockfile → npm ci 分支自然生效；核对 bundle 产物。
- `pi_compat.py` PI_MIN_VENDOR_TAG → v1.5.1；`PI_UPSTREAM_COMPAT_MATRIX.md` 更新 pin/差异表/
  版本史 + 新契约项 C11-C16（见下）；新 ADR（无碰撞编号，目标 0288/0289 段，落前核对）。
- 验证：build-vendor → pi_doctor（含 rpc live 域）→ vendor contract 7 测 + schema coercion golden
  + extension hardening 全绿。任何 v1.5.1 行为漂移在此暴露并适配。
- 新契约项（在 C1-C10 上加性）：
  - C11 内置扩展在 `--no-builtin-tools` 下仍加载（tool_search/codemode 可经 setActiveTools 激活）
  - C12 tool_search merge 语义（激活不打发现丢前门）+ BM25 中文/$ref 检索
  - C13 嵌套 tool_execution_* 带 parentToolCallId + nestedCalls 归父
  - C14 clear_queue RPC + abort-waits-idle + 双信号停机语义（stop 窗口需 ≥ dispose 秒级）
  - C15 dump v4 原生 exposure/namespace/annotations/reachesTools 字段
  - C16 根 package-lock.json → npm ci 可复现构建

### Wave 2 — W03 原生工具封套（dump v4 + 扩展注册原生字段）
- `pi_native_surface`：`_pi_tool_definition` 投影 `exposure`（native 前门+proxy=direct；长尾超集
  =deferred）、`namespace`（gis.<域> 从 descriptor 域派生，有界）、`annotations`（readOnlyHint/
  destructiveHint/idempotentHint 从 side_effect 派生 — **只作提示不作授权**）、可选 prompt guidance
  透传。dump 形状 v4 = v3 + `exposure` 字段（additive；v3 解析方不受影响）。指纹口径不变
  （PiToolSpecV1 canonical JSON），exposure 是投影决策不入指纹。
- `index.mjs`：registerTool 透传 exposure/namespace/annotations；常驻激活集纳入
  `tool_search` + `codemode`（W04 一并）；MAX_ACTIVE 会计。
- 验收：注册面 vs 声明面两集合可观测（doctor/诊断）；oversized schema 仍整工具逐出（已有）；
  资格读取故障不得把受限工具转可执行（已有单测回归）。

### Wave 3 — W04+W05 发现闭环与授权一致性（核心纵切：A2/A3/A6/A7）
- 扩展 alwaysActive += tool_search, codemode（保留 env kill-switch PI_NATIVE_DISCOVERY=0 回退）。
- 后端 discovery grant：`pi_surface_receipt` 增加 `record_discovered_tool(session, turn, tool,
  fingerprint)`（turn 内 additive、幂等、有界；来源=gate 路径，非 LLM 自授权——授权集仍=
  spawn 注册超集 ∩ tier/input/owner/writer 闸）。`enforce_callback_scope` 扩为 selected ∪
  discovered；`PI_SURFACE_SCOPE_ENFORCE` 默认改 ON（kill-switch 保留），因为发现现有合法路径。
- codemode 的 reachesTools/预算约束沿用 vendor 内建；本任务不自制 JS 引擎。
- 验收：真实 tool_search 发现 → 下一模型请求看到合法 schema → 直调回到 Python 同一网关；
  撤权/过期 schema（A6）拒绝；bash/read/write/edit（A7）不可达（未注册）。

### Wave 4 — W06 嵌套调用/部分失败/写幂等
- `pi_event_mapper`：事件带 parentToolCallId → 投影为 child step（`parent_tool_call_id` 字段进
  SSE payload，additive）；父 step_result 汇总 bounded nested 结果；重复 child end 不重复展示
  （复用 G01 SSE 幂等）；父子 AbortSignal 已由 vendor 传递（execute 的 _signal）。
- 幂等：嵌套写调用经同一 executed-set/operation identity；重放（A5）不重复结算（现有 durable
  幂等键回归）。断线重放零 LLM 调用（J7 已有）保持。
- 前端：child step 嵌套展示（最小改动：tool row 下挂 child 行，不触 #2032 主题/布局）。

### Wave 5 — W07 取消/队列/有序停机
- `PiBridge.abort(source="user")` 语义升级：完整停止 = `clear_queue` → `abort`（v1.5.1 Esc 语义）；
  steer/follow-up 与完整停止区分（clear_queue 只在用户级停止时清）。
- `PiRpcClient.stop()`：stdin EOF（优雅关输入）→ 有界等待（默认 ~8s，env 可调）→ terminate →
  有界等待 → kill → 收尸；记录控制窗口。对齐 v1.5.1 双信号语义（我们绝不双发信号=不强退清理）。
- 验收：A8/A9/A14（EOF+SIGTERM 相邻、工具执行中、compaction 中终态确定）。

### Wave 6 — W08 模型配置生效（#2061）
- 单一解析点接入：`_spawn_process` 写 models.json/settings.json 改用
  `model_config.resolve_llm_config()`（Settings ⊕ runtime override）。
- 代际机制：服务端记录 spawn 时的 config generation（对 base_url/model/api_key 指纹化，日志只留
  不可逆摘要）；`update_config` 后期望代 ≠ 某 worker 生效代 → 标记该 worker 待换代（下一 turn
  前 respawn）；当前 turn 不切半套；失败保留旧代并如实披露「未生效」。
- 生产限制：保留既有 env 约束（生产禁动态改配置的路由检查按 issue 语义核对，不放松）。
- 验收：A10 双 stub provider 记录真实请求的 model/端点/测试 key 指纹；get_config 字段变化不算证据。

### Wave 7 — W09 compaction/长会话恢复
- 扩展挂 `session_before_compact`/`session_after_compact`（记账/披露，不篡改 leading system
  message）；GIS 状态重建走 typed assembly 既有 provider 框架加一个有界 versioned「GIS 状态块」
  （用户要求/当前图层/隐藏状态/待办/既有结果 refs/人工锁定 refs —— 全部引用权威源，不凭 summary
  授予修改权）。
- worker 重启（A12）：respawn 后首个 turn 的重建块从 SessionPlan/artifact/MapSpec 权威读取；
  不重放有副作用工具。
- 验收：A11（压缩后只改颜色、隐藏层与锁定保留）、A13（同 worker 双 session 交替不泄漏 — 已有
  pool affinity 回归，补 GIS 重建块变体）。

### Wave 8 — W10 可观测 + W11 旅程 + W12 收口
- 诊断面：pi_doctor 增 discovery 域（tool_search/codemode 声明可见性）、config generation 域；
  /health 与 task_start 披露维持既有词汇（native/unavailable）。
- 旅程扩展（`test_pi_stub_llm_journeys.py` driver + TURN_SCRIPTS 增补）：A2 发现、A4 codemode
  并行、A5 重放、A6 撤权、A7 越权、A8/A9 取消竞态、A10 配置换代、A11 压缩、A14 停机。真实
  rpc-entry + stub LLM；不 mock dispatcher/gate/MapSpec。
- 前端最小改动 + typecheck/lint/vitest；一个真实浏览器旅程（若沙箱可行，否则记为未运行）。
- W12：隔离包验证（bundle+jiti only 目录真实启动 + 回调）、基线对比（旧/新 pin 首轮 schema
  字节、发现后字节、首工具耗时、取消完成时间、峰值 RSS）一次性采足；更新 PI_RUNTIME_CONTRACT、
  compat matrix、升级/回退/排错/新工具投影开发文档。

## 红线（全程）

- 不新增第二 agent loop / 工具注册中心 / MapSpec store / 撤销栈 / evidence store。
- setActiveTools 只是声明控制；权限 = bridge secret + turn token + live-turn + dispatcher 闸。
- 不在 vendor 内打补丁；不编造 RPC（无 reload）。
- 语义四态 unknown/not_evaluated/blocked/partial 保持；assistant 最终文字不覆盖地图验收。
- 资源纪律：重型构建 -j2（flock webgis-heavy-stage.lock）；vitest/pytest workers 1-2。
- 提交粒度：每波 ≥1 commit（conventional + 中文主题）；不 stash；不强推。
